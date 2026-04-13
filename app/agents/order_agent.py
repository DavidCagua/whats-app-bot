"""
Order agent: planner (intent) -> executor (one tool) -> response generator.
Backend is single source of truth; response is generated from actual tool result and cart state.
"""

import json
import os
import logging
import re
import uuid
import time
from typing import Any, Dict, List, Optional
from datetime import date
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .base_agent import BaseAgent, AgentOutput
from ..services.order_tools import order_tools
from ..orchestration.order_flow import (
    execute_order_intent,
    INTENT_CHAT,
    INTENT_GREET,
    RESULT_KIND_CHAT,
    RESULT_KIND_MENU_CATEGORIES,
    RESULT_KIND_PRODUCTS_LIST,
    RESULT_KIND_PRODUCT_DETAILS,
    RESULT_KIND_CART_CHANGE,
    RESULT_KIND_CART_VIEW,
    RESULT_KIND_DELIVERY_STATUS,
    RESULT_KIND_ORDER_PLACED,
    RESULT_KIND_NEEDS_CLARIFICATION,
    RESULT_KIND_USER_ERROR,
    RESULT_KIND_INTERNAL_ERROR,
    CART_ACTION_ADDED,
    CART_ACTION_REMOVED,
    CART_ACTION_UPDATED_QUANTITY,
    CART_ACTION_UPDATED_NOTES,
    CART_ACTION_REPLACED,
    CART_ACTION_NOOP,
)
from ..database.conversation_service import conversation_service
from ..database.booking_service import booking_service
from ..services.tracing import tracer


PLANNER_SYSTEM_TEMPLATE = """Eres un clasificador de intención para un bot de pedidos. Dado el estado actual del pedido y el mensaje del usuario, devuelves EXACTAMENTE una intención y sus parámetros en JSON.

Estado actual: {order_state}
Productos YA en el pedido (NO los incluyas en ADD_TO_CART a menos que el usuario pida explícitamente más cantidad): {cart_summary}

Intenciones válidas: GREET, GET_MENU_CATEGORIES, LIST_PRODUCTS, SEARCH_PRODUCTS, GET_PRODUCT, ADD_TO_CART, VIEW_CART, UPDATE_CART_ITEM, REMOVE_FROM_CART, PROCEED_TO_CHECKOUT, GET_CUSTOMER_INFO, SUBMIT_DELIVERY_INFO, PLACE_ORDER, CHAT.

Reglas de menú y búsqueda (importante):
- GET_MENU_CATEGORIES: cuando el usuario pregunta qué hay, qué tienes en general, o qué categorías hay (ej. "qué tienes", "qué hay en el menú"). Sin params.
- LIST_PRODUCTS con category: cuando pregunta qué tienes EN UNA CATEGORÍA (ej. "qué tienes de bebidas", "qué hamburguesas tienes", "qué bebidas hay"). Siempre pasa params: {{"category": "bebidas"}} o "hamburguesas", "BEBIDAS", etc. category vacío = menú completo. IMPORTANTE: frases implícitas también cuentan — "qué hay para tomar", "qué tienen para tomar", "algo para beber", "qué tienen de beber" → LIST_PRODUCTS con category "bebidas". "qué hay para comer", "algo de comida" (sin más contexto) → LIST_PRODUCTS sin category (menú completo) o GET_MENU_CATEGORIES si prefiere ver categorías.
- SEARCH_PRODUCTS con query: cuando el usuario NOMBRA un producto o ingrediente (ej. "quiero barracuda", "tienes coca cola", "algo con queso azul"). No uses para preguntas de categoría; para "qué tienes de X" usa LIST_PRODUCTS con category.
- GET_PRODUCT con product_name: cuando pregunta qué trae o qué tiene UN producto específico en singular (ej. "qué trae la barracuda", "qué tiene la montesa").
- LIST_PRODUCTS (con la última categoría mostrada) cuando el usuario pide detalles de VARIOS/TODOS los productos ya listados — en plural o colectivo (ej. "qué tienen cada una", "qué trae cada una de esas hamburguesas", "dame los detalles de todas", "qué ingredientes tiene cada una"). NO uses GET_PRODUCT en estos casos: el usuario quiere ver todo el grupo, no uno solo.

Otras reglas:
- REGLA DE PRIORIDAD (más importante que las demás): si el mensaje NOMBRA uno o más productos del menú (aunque esté acompañado de un saludo, de la palabra "domicilio", "pedido", "por favor", o de una lista con saltos de línea), SIEMPRE clasifica como ADD_TO_CART con los items correspondientes. El saludo y palabras como "domicilio"/"pedido" son contexto, NO intención — se ignoran para la clasificación cuando hay productos nombrados. CHAT y GREET se usan SOLO cuando NO hay ningún producto en el mensaje.
- GREET SOLO si el mensaje es únicamente un saludo ("hola", "buenas", "buenos días", "buenas noches") SIN ninguna mención de producto, cantidad o intención de pedir. Si el usuario mezcla saludo con un producto específico ("hola quiero una barracuda") → usa ADD_TO_CART o SEARCH_PRODUCTS directamente, NO GREET.
- Si el usuario expresa intención de pedir u ordenar SIN nombrar ningún producto específico (ej. "para un domicilio", "quiero pedir", "quiero hacer un pedido", "buenas, un domicilio por favor", "me pueden atender"): usa CHAT. El usuario probablemente ya sabe qué quiere; solo invítalo a decir su pedido. NO uses ADD_TO_CART, SEARCH_PRODUCTS ni GET_MENU_CATEGORIES porque no hay producto ni pregunta por el menú. IMPORTANTE: esta regla aplica SOLO si no hay productos nombrados — si hay aunque sea un producto, gana la regla de prioridad de arriba.
- Si pide agregar uno o más productos: ADD_TO_CART. Para un solo producto: params con "product_name" (o "product_id"), "quantity" y opcionalmente "notes" para instrucciones especiales (ej. "sin cebolla", "sin morcilla", "extra salsa"). Para varios productos: params con "items": [ {{"product_name": "NOMBRE", "quantity": 1, "notes": "..."}}, ... ]. Ejemplo con nota: "una barracuda sin cebolla caramelizada" → {{"intent": "ADD_TO_CART", "params": {{"product_name": "BARRACUDA", "quantity": 1, "notes": "sin cebolla caramelizada"}}}}. Ejemplo varios: "dame una montesa y una booster" → {{"intent": "ADD_TO_CART", "params": {{"items": [{{"product_name": "MONTESA", "quantity": 1}}, {{"product_name": "BOOSTER", "quantity": 1}}]}}}}. Ejemplo saludo + pedido multi-producto: "hola buenas un domicilio por favor, 2 betas, 1 barracuda, 1 biela fries" → {{"intent": "ADD_TO_CART", "params": {{"items": [{{"product_name": "BETA", "quantity": 2}}, {{"product_name": "BARRACUDA", "quantity": 1}}, {{"product_name": "BIELA FRIES", "quantity": 1}}]}}}}. Ejemplo con saltos de línea: "hola buenas tardes un domicilio por favor\\n2 betas\\n1 barracuda\\n1 biela fries" → mismo resultado (los saltos de línea son solo formato).
- MODIFICACIONES DE INGREDIENTES en producto YA AGREGADO al pedido (ej. "sin morcilla", "para que no le pongan cebolla", "quítale el queso"): usa UPDATE_CART_ITEM con "product_name" del producto en el pedido y "notes" con la instrucción. Ejemplo: pedido tiene PICADA y usuario dice "para que no le pongan morcilla" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "PICADA", "notes": "sin morcilla"}}}}. NUNCA uses ADD_TO_CART para modificar un ingrediente de un producto existente.
- REEMPLAZO POR VARIANTE / SABOR / TIPO de un producto YA en el pedido (ej. "la soda que sea de frutos rojos", "mejor la hamburguesa doble", "cámbiala por la de pollo", "que sea la Corona", "la cerveza que sea Poker"): usa UPDATE_CART_ITEM con "product_name" = nombre del producto ACTUAL en el carrito, y "new_product_name" = nombre completo del producto NUEVO combinando el nombre actual con la variante. Ejemplo: carrito tiene "Soda" y usuario dice "la soda que sea de frutos rojos" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "Soda", "new_product_name": "Soda Frutos rojos"}}}}. Ejemplo: carrito tiene "Michelada" y usuario dice "que sea con Corona" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "Michelada", "new_product_name": "Corona michelada"}}}}. Distingue de `notes`: usa `notes` SOLO para exclusiones/añadidos de ingredientes (ej. "sin morcilla", "extra salsa"), NUNCA para elegir otra variante del producto. NUNCA uses ADD_TO_CART para un reemplazo: UPDATE_CART_ITEM con new_product_name maneja la sustitución atómica.
- Si pide quitar un producto del pedido completamente ("elimina la malteada", "quita eso", "no quiero la coca cola"): REMOVE_FROM_CART con "product_name". Ejemplo: "elimina la malteada" → {{"intent": "REMOVE_FROM_CART", "params": {{"product_name": "malteada"}}}}.
- Si dice "listo", "procedamos", "confirmar": PROCEED_TO_CHECKOUT.
- Si ya están en recolección de datos (COLLECTING_DELIVERY): usa GET_CUSTOMER_INFO cuando necesites saber qué tenemos o qué falta (ej. usuario dice "listo", "ok", o para mostrar confirmación). Usa SUBMIT_DELIVERY_INFO cuando el usuario proporcione uno o más de: address, phone, name, payment_method; params pueden ser parciales, ej. {{"address": "Calle 1"}}, {{"payment_method": "Efectivo"}}, {{"name": "Juan", "phone": "+57..."}}.
- Si el usuario corrige dirección, teléfono o medio de pago (ej. "no es esa dirección, es calle X", "mejor a esta dirección", "el teléfono es otro"): usa SUBMIT_DELIVERY_INFO con el valor nuevo, ej. {{"address": "calle 19#29-99"}}.
- Si el usuario indica que su teléfono es el MISMO desde el que está escribiendo (ej. "este número", "este mismo", "el mismo", "mi whatsapp", "con este mismo", "el de whatsapp", "al que te estoy escribiendo"): usa SUBMIT_DELIVERY_INFO con `phone` igual al marcador literal `<SENDER>`. Ejemplo: {{"intent": "SUBMIT_DELIVERY_INFO", "params": {{"phone": "<SENDER>"}}}}. El backend sustituirá el marcador por el número real del remitente. NUNCA inventes un número.
- Si ya tienen todos los datos y confirman pedido: PLACE_ORDER.
- Si solo conversa: CHAT.

Responde ÚNICAMENTE con un JSON válido, sin markdown ni texto extra: {{"intent": "NOMBRE", "params": {{}}}}
"""

RESPONSE_GENERATOR_SYSTEM = """Generas la respuesta del asistente en español colombiano, amigable y breve.

Reglas críticas:
- NUNCA afirmes que agregaste, quitaste o modificaste algo en el pedido si la intención ejecutada no fue ADD_TO_CART, REMOVE_FROM_CART o UPDATE_CART_ITEM con éxito. Solo describe cambios que el backend confirmó.
- Si se ejecutó add/remove/update con éxito, incluye el resumen del pedido actual que te doy (es la verdad del backend).
- Usa solo la información del resultado de la herramienta y del resumen del pedido; no inventes datos.
- Si hubo error, explica brevemente y sugiere qué hacer.
- Después de un ADD_TO_CART exitoso: (1) confirma lo que se agregó, (2) muestra el resumen del pedido actual, (3) sugiere el siguiente paso: pregunta si desea agregar algo más (ej. bebida) o si procede con el pedido (ej. "¿Te gustaría agregar alguna bebida o procedemos con el pedido?").
- Búsqueda por ingrediente: cuando el resultado de la herramienta incluya descripciones de productos (varias líneas por producto) y el usuario preguntó por un ingrediente o tipo de plato (ej. "algo con queso azul", "hamburguesa con pollo"), menciona primero y de forma explícita el producto cuya descripción coincida con lo que pidió (ej. "La que lleva queso azul es la MONTESA: ...") y luego puedes listar brevemente otras opciones si aplica.
- Datos de entrega: NUNCA digas "Tengo esta dirección, teléfono y tipo de pago" a menos que el resultado de la herramienta contenga exactamente "DELIVERY_STATUS" y "all_present=true". Si el resultado es "OK_COLLECTING_DELIVERY" (sin DELIVERY_STATUS), responde pidiendo los datos: "Para continuar con tu pedido necesito: nombre, dirección, teléfono y medio de pago. ¿Me los indicas?". Si el resultado tiene DELIVERY_STATUS y all_present=true, confirma incluyendo los valores reales (dirección, teléfono, medio de pago) en el mensaje: "Tengo esta dirección: [valor], teléfono [valor] y pago [valor]. ¿Gustas proceder o quieres enviarla a otra dirección?". Si DELIVERY_STATUS tiene missing= o all_present=false: pide SOLO lo que falta (ej. "Me falta: teléfono y medio de pago. ¿Me los indicas?") o todo si faltan todos; NUNCA en ese caso sugieras "proceder con el pedido" ni "agregar algo más" hasta que todos los datos estén completos.
- Ubicación y datos del negocio: si el usuario pregunta dónde estamos ubicados, horarios, teléfono de contacto o dirección del local, responde usando ÚNICAMENTE la "Información del negocio" que te doy a continuación. Si esa información está vacía o dice "no configurada", di que por el momento no tienes esa información a mano y que puede preguntar por el menú o hacer su pedido.
- Combos / hamburguesas con papas: si el usuario pregunta si tienen combos, si las hamburguesas vienen con papas o si incluyen papas, responde SIEMPRE usando la sección "Reglas y contexto del negocio" de la Información del negocio (aunque la intención ejecutada haya sido GET_MENU_CATEGORIES o GET_PRODUCT). No digas "no encontré información" ni solo listes categorías; da la respuesta de las reglas (ej. todas las hamburguesas vienen con papas, bebida aparte).
"""


def _format_business_info_for_prompt(business_context: Optional[Dict]) -> str:
    """Format address, phone, hours from business_context for the response generator."""
    if not business_context or not business_context.get("business"):
        return "Información del negocio: (no configurada)."
    raw_settings = business_context["business"].get("settings")
    # Support both dict and None; JSONB can sometimes be dict-like
    settings = dict(raw_settings) if raw_settings is not None else {}
    if not isinstance(settings, dict):
        settings = {}
    address = (settings.get("address") or settings.get("Address") or "").strip()
    phone = (settings.get("phone") or "").strip()
    city = (settings.get("city") or "").strip()
    state = (settings.get("state") or "").strip()
    country = (settings.get("country") or "").strip()
    business_id = business_context.get("business_id")
    parts = []
    if address:
        parts.append(f"Dirección: {address}")
    if city or state or country:
        loc = ", ".join(filter(None, [city, state, country]))
        if loc:
            parts.append(f"Ciudad/país: {loc}")
    if phone:
        parts.append(f"Teléfono: {phone}")
    if business_id:
        try:
            rules = booking_service.get_availability(str(business_id))
            if rules:
                day_names = {
                    0: "Domingo",
                    1: "Lunes",
                    2: "Martes",
                    3: "Miércoles",
                    4: "Jueves",
                    5: "Viernes",
                    6: "Sábado",
                }
                hour_lines = []
                for rule in sorted(rules, key=lambda x: x.get("day_of_week", 0)):
                    day_label = day_names.get(rule.get("day_of_week", -1), "Día")
                    if not rule.get("is_active", True):
                        hour_lines.append(f"  {day_label}: cerrado")
                        continue
                    hour_lines.append(
                        f"  {day_label}: {rule.get('open_time', '')} - {rule.get('close_time', '')}"
                    )
                if hour_lines:
                    parts.append("Horarios:\n" + "\n".join(hour_lines))
        except Exception:
            pass
    # One clear line for location questions: address if set, else city/state/country
    location_parts = []
    if address:
        location_parts.append(address)
    if city or state or country:
        location_parts.append(", ".join(filter(None, [city, state, country])))
    if location_parts:
        parts.append("Ubicación (para preguntas 'dónde están'): " + " ".join(location_parts))
    ai_prompt = (settings.get("ai_prompt") or "").strip()
    if ai_prompt:
        parts.append("IMPORTANTE: Reglas y contexto del negocio (usa para preguntas sobre combos, hamburguesas con papas, etc.):\n" + ai_prompt)
    if not parts:
        return "Información del negocio: (no configurada)."
    return "Información del negocio:\n" + "\n".join(parts)


def _parse_planner_response(text: str) -> Dict[str, Any]:
    """Extract intent and params from planner LLM response (JSON only or embedded)."""
    text = (text or "").strip()
    # Try raw JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in text
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"intent": INTENT_CHAT, "params": {}}


class OrderAgent(BaseAgent):
    """Order agent: planner (intent) -> executor (one tool) -> response from real state."""

    agent_type = "order"

    def __init__(self):
        self._llm = None
        logging.info("[ORDER_AGENT] Initialized with planner + executor + response generator (LLM lazy)")

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.3,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._llm

    def get_tools(self):
        return order_tools

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        """Used by response generator for business name/menu_url context."""
        business_name = "el restaurante"
        menu_url = ""
        if business_context and business_context.get("business"):
            biz = business_context["business"]
            business_name = biz.get("name") or business_name
            settings = biz.get("settings") or {}
            menu_url = settings.get("menu_url") or ""
        return f"Negocio: {business_name}. Menu URL: {menu_url or 'no configurado'}. Fecha: {current_date}."

    def _build_response_prompt(
        self,
        result_kind: str,
        exec_result: Dict[str, Any],
        message_body: str,
        business_context: Optional[Dict],
        cart_summary_after: str,
    ) -> tuple:
        """
        Build (response_system, resp_input) pair tailored to the result_kind.
        Each branch gets structured data (never raw tool strings) and a dedicated tone guide.
        """
        biz_info = _format_business_info_for_prompt(business_context)
        base_system = (
            "Eres el asistente de pedidos de un restaurante colombiano. Hablas en español colombiano, "
            "cálido, breve y natural — como un mesero profesional que conoce su menú. "
            "Usa SOLO los datos que te doy; no inventes productos, precios, ni confirmes acciones que no ocurrieron. "
            "Respuestas breves (1-4 líneas típicamente). Evita frases robóticas.\n\n"
            + biz_info
        )

        def money(x: Any) -> str:
            try:
                return f"${int(x):,}".replace(",", ".")
            except Exception:
                return f"${x}"

        if result_kind == RESULT_KIND_NEEDS_CLARIFICATION:
            options = exec_result.get("options") or []
            requested = exec_result.get("requested_name") or "ese producto"
            opts_lines = "\n".join(f"- {o.get('name')} ({money(o.get('price'))})" for o in options)
            system = base_system + (
                "\n\nSITUACIÓN: El cliente pidió un producto con varias variantes. Necesitas saber cuál quiere ANTES de agregarlo. "
                "Esto NO es un error — es una pregunta normal de mesero.\n"
                "REGLAS:\n"
                "- PROHIBIDO: 'no se pudo', 'no pude', 'error', 'problema', 'disculpa', 'lo siento', 'falló'.\n"
                "- PROHIBIDO: 'agregué', 'listo', 'ya está', 'añadí' — el pedido NO cambió.\n"
                "- NO repitas el resumen del pedido actual.\n"
                "- Usa SOLO los nombres y precios de la lista, exactos.\n"
                "- 1-3 líneas total."
            )
            inp = (
                f"Cliente dijo: {message_body}\n"
                f"Producto solicitado: {requested}\n"
                f"Opciones disponibles (usa exactamente estos nombres y precios):\n{opts_lines}\n"
                f"Tarea: presenta las opciones de forma natural y pregunta cuál prefiere."
            )
            return system, inp

        if result_kind == RESULT_KIND_CHAT:
            system = base_system + (
                "\n\nSITUACIÓN: El cliente está conversando sin pedir una acción específica. "
                "Si mencionó que quiere hacer un pedido pero no dijo qué, invítalo a decirte qué quiere ordenar. "
                "Si saludó o hizo small talk, responde amable y breve (1-2 líneas) y ofrécele ayuda con su pedido. "
                "NO listes el menú a menos que lo pida."
            )
            inp = f"Cliente dijo: {message_body}\nEstado del pedido: {cart_summary_after}"
            return system, inp

        if result_kind == RESULT_KIND_MENU_CATEGORIES:
            categories = exec_result.get("categories") or []
            cats_lines = "\n".join(f"- {c}" for c in categories)
            system = base_system + (
                "\n\nSITUACIÓN: El cliente preguntó qué hay en el menú. Tienes las categorías disponibles. "
                "REGLAS:\n"
                "- Presenta las categorías de forma amigable (puedes traducirlas al español natural, ej. HAMBURGUESAS → hamburguesas).\n"
                "- Invítalo a elegir una categoría para ver los productos (ej. '¿quieres ver las hamburguesas o las bebidas?').\n"
                "- NO listes productos individuales — solo categorías.\n"
                "- 1-3 líneas."
            )
            inp = f"Cliente dijo: {message_body}\nCategorías disponibles:\n{cats_lines}"
            return system, inp

        if result_kind == RESULT_KIND_PRODUCTS_LIST:
            products = exec_result.get("products") or []
            query_label = exec_result.get("query_label")
            category_label = exec_result.get("category_label")
            if not products:
                label = query_label or category_label or "eso"
                system = base_system + (
                    "\n\nSITUACIÓN: No hay productos que coincidan con lo que el cliente pidió. "
                    "Dile amablemente (sin 'lo siento') y ofrécele ver las categorías del menú. 1-2 líneas."
                )
                inp = f"Cliente buscó: {label}\nNo hay coincidencias."
                return system, inp
            prods_lines = "\n".join(
                f"- {p.get('name')} ({money(p.get('price'))})"
                + (f" — {p.get('description')}" if p.get("description") else "")
                for p in products
            )
            context_label = ""
            if category_label:
                context_label = f"Categoría: {category_label}"
            elif query_label:
                context_label = f"Búsqueda: {query_label}"
            system = base_system + (
                "\n\nSITUACIÓN: El cliente pidió ver una lista de productos. "
                "REGLAS:\n"
                "- Presenta los productos de forma clara con nombre y precio.\n"
                "- Si el producto trae descripción en los datos, INCLÚYELA SIEMPRE (1 línea breve por producto). Nunca omitas descripciones aunque la lista sea larga.\n"
                "- Si NO hay descripciones disponibles, lista solo nombre y precio.\n"
                "- Si hay descripciones y el cliente preguntó por un ingrediente, menciona primero el producto cuya descripción coincide.\n"
                "- Termina invitando a ordenar o a preguntar por alguno en particular.\n"
                "- NUNCA muestres IDs ni códigos internos."
            )
            inp = f"Cliente dijo: {message_body}\n{context_label}\nProductos disponibles:\n{prods_lines}"
            return system, inp

        if result_kind == RESULT_KIND_PRODUCT_DETAILS:
            product = exec_result.get("product") or {}
            desc = product.get("description") or "(sin descripción)"
            system = base_system + (
                "\n\nSITUACIÓN: El cliente preguntó por los detalles de un producto. "
                "REGLAS:\n"
                "- Di el nombre, precio y describe qué trae en 1-2 líneas.\n"
                "- Termina preguntando si lo quiere agregar al pedido.\n"
                "- NO inventes ingredientes fuera de la descripción dada."
            )
            inp = (
                f"Cliente dijo: {message_body}\n"
                f"Producto: {product.get('name')}\n"
                f"Precio: {money(product.get('price'))}\n"
                f"Descripción: {desc}"
            )
            return system, inp

        if result_kind == RESULT_KIND_CART_CHANGE:
            cc = exec_result.get("cart_change") or {}
            action = cc.get("action") or ""
            added = cc.get("added") or []
            removed = cc.get("removed") or []
            updated = cc.get("updated") or []
            cart_after = cc.get("cart_after") or []
            total_after = cc.get("total_after") or 0

            def fmt_items(items):
                return "\n".join(
                    f"- {it.get('quantity')}x {it.get('name')}"
                    + (f" ({it.get('notes')})" if it.get("notes") else "")
                    for it in items
                )

            cart_lines = fmt_items(cart_after) or "(vacío)"

            if action == CART_ACTION_NOOP:
                system = base_system + (
                    "\n\nSITUACIÓN: El cliente pidió modificar el pedido pero nada cambió "
                    "(tal vez ya estaba así, o no se encontró el producto). "
                    "Dile amablemente el estado actual y pregúntale qué quiere hacer. "
                    "NO uses 'error' ni 'lo siento'. 1-2 líneas."
                )
                inp = f"Cliente dijo: {message_body}\nPedido actual (sin cambios):\n{cart_lines}\nTotal: {money(total_after)}"
                return system, inp

            if action == CART_ACTION_ADDED:
                situation = "El cliente agregó productos al pedido."
                change_desc = f"Agregado:\n{fmt_items(added)}"
            elif action == CART_ACTION_REMOVED:
                situation = "El cliente quitó productos del pedido."
                change_desc = f"Quitado:\n{fmt_items(removed)}"
            elif action == CART_ACTION_UPDATED_QUANTITY:
                situation = "El cliente actualizó la cantidad de un producto."
                change_desc = f"Actualizado:\n{fmt_items(updated)}"
            elif action == CART_ACTION_UPDATED_NOTES:
                situation = "El cliente cambió las instrucciones de un producto (ej. 'sin cebolla')."
                change_desc = f"Actualizado (notas):\n{fmt_items(added) or fmt_items(updated)}"
            elif action == CART_ACTION_REPLACED:
                situation = "El cliente reemplazó un producto por otro."
                change_desc = f"Quitado:\n{fmt_items(removed)}\nAgregado:\n{fmt_items(added)}"
            else:
                situation = "Cambio en el pedido."
                change_desc = f"Actualizado:\n{fmt_items(updated) or fmt_items(added) or fmt_items(removed)}"

            system = base_system + (
                f"\n\nSITUACIÓN: {situation}\n"
                "REGLAS:\n"
                "- Confirma brevemente el cambio que hizo el backend (está en 'Cambio realizado').\n"
                "- Muestra el resumen del pedido actual.\n"
                "- Sugiere el siguiente paso: preguntar si quiere agregar algo más (ej. bebida si no tiene una) o procedemos con el pedido.\n"
                "- NO inventes productos ni precios — usa solo los datos dados.\n"
                "- 2-5 líneas."
            )
            inp = (
                f"Cliente dijo: {message_body}\n"
                f"Cambio realizado:\n{change_desc}\n"
                f"Pedido actual:\n{cart_lines}\n"
                f"Subtotal: {money(total_after)}"
            )
            return system, inp

        if result_kind == RESULT_KIND_CART_VIEW:
            cv = exec_result.get("cart_view") or {}
            items = cv.get("items") or []
            if cv.get("is_empty") or not items:
                system = base_system + (
                    "\n\nSITUACIÓN: El cliente pidió ver su pedido, pero está vacío. "
                    "Invítalo amablemente a pedir algo (sugiérele preguntar por categorías). 1-2 líneas."
                )
                inp = f"Cliente dijo: {message_body}\nPedido: vacío"
                return system, inp
            items_lines = "\n".join(
                f"- {it.get('quantity')}x {it.get('name')}"
                + (f" ({it.get('notes')})" if it.get("notes") else "")
                + f" — {money(int(it.get('price') or 0) * int(it.get('quantity') or 0))}"
                for it in items
            )
            system = base_system + (
                "\n\nSITUACIÓN: El cliente quiere ver su pedido actual. "
                "REGLAS:\n"
                "- Muestra cada ítem con cantidad, nombre, notas (si hay) y precio.\n"
                "- Muestra subtotal, domicilio y total.\n"
                "- Pregunta si quiere agregar algo más o proceder.\n"
                "- NO muestres IDs internos."
            )
            inp = (
                f"Cliente dijo: {message_body}\n"
                f"Pedido actual:\n{items_lines}\n"
                f"Subtotal: {money(cv.get('subtotal'))}\n"
                f"Domicilio: {money(cv.get('delivery_fee'))}\n"
                f"Total: {money(cv.get('total'))}"
            )
            return system, inp

        if result_kind == RESULT_KIND_DELIVERY_STATUS:
            ds = exec_result.get("delivery_status") or {}
            if ds.get("all_present"):
                system = base_system + (
                    "\n\nSITUACIÓN: Ya tenemos todos los datos de entrega del cliente. "
                    "Confírmale los datos que tenemos y pregúntale si gusta proceder o quiere cambiar algo. "
                    "FORMATO: 'Tengo esta dirección: [addr], teléfono [phone] y pago [payment]. ¿Procedemos o quieres cambiar algo?'. "
                    "1-3 líneas."
                )
                inp = (
                    f"Cliente dijo: {message_body}\n"
                    f"Datos actuales:\n"
                    f"- Nombre: {ds.get('name') or '(no registrado)'}\n"
                    f"- Dirección: {ds.get('address')}\n"
                    f"- Teléfono: {ds.get('phone')}\n"
                    f"- Pago: {ds.get('payment_method')}"
                )
                return system, inp
            missing = ds.get("missing") or []
            missing_es = {"name": "nombre", "address": "dirección", "phone": "teléfono", "payment": "medio de pago"}
            missing_labels = [missing_es.get(m, m) for m in missing]
            system = base_system + (
                "\n\nSITUACIÓN: Faltan algunos datos de entrega. "
                "REGLAS:\n"
                "- Pide SOLO los datos que faltan (nunca pidas de más).\n"
                "- Si ya tenemos alguno, menciónalo brevemente (ej. 'ya tengo tu dirección').\n"
                "- NO sugieras 'proceder con el pedido' hasta tener todos los datos.\n"
                "- 1-3 líneas."
            )
            inp = (
                f"Cliente dijo: {message_body}\n"
                f"Datos ya registrados:\n"
                f"- Nombre: {ds.get('name') or '(falta)'}\n"
                f"- Dirección: {ds.get('address') or '(falta)'}\n"
                f"- Teléfono: {ds.get('phone') or '(falta)'}\n"
                f"- Pago: {ds.get('payment_method') or '(falta)'}\n"
                f"Faltan: {', '.join(missing_labels) if missing_labels else '(ninguno)'}"
            )
            return system, inp

        if result_kind == RESULT_KIND_ORDER_PLACED:
            op = exec_result.get("order_placed") or {}
            oid = op.get("order_id_display") or ""
            items = op.get("items") or []
            items_lines = "\n".join(
                f"- {it.get('quantity')}x {it.get('name')}"
                + (f" ({it.get('notes')})" if it.get("notes") else "")
                for it in items
            )
            system = base_system + (
                "\n\nSITUACIÓN: El pedido fue confirmado exitosamente. "
                "REGLAS:\n"
                "- Celebra brevemente (ej. '¡Listo!').\n"
                "- Muestra el número de pedido, items, subtotal, domicilio y total.\n"
                "- Dile que pronto se comunicarán para coordinar la entrega.\n"
                "- 3-6 líneas."
            )
            inp = (
                f"Pedido confirmado.\n"
                f"Número: #{oid}\n"
                f"Items:\n{items_lines}\n"
                f"Subtotal: {money(op.get('subtotal'))}\n"
                f"Domicilio: {money(op.get('delivery_fee'))}\n"
                f"Total: {money(op.get('total'))}"
            )
            return system, inp

        if result_kind == RESULT_KIND_USER_ERROR:
            err = exec_result.get("error_message") or "No pude procesar eso."
            system = base_system + (
                "\n\nSITUACIÓN: Ocurrió una situación que requiere guiar al cliente (ej. pedido vacío, producto no encontrado). "
                "REGLAS:\n"
                "- Dile lo que pasa de forma amable y natural, SIN usar 'error', 'falló', 'disculpa', 'lo siento', 'no pude'.\n"
                "- Ofrece el siguiente paso útil (ej. 'dime qué te gustaría pedir', 'revisa el menú').\n"
                "- 1-2 líneas."
            )
            inp = f"Cliente dijo: {message_body}\nSituación: {err}"
            return system, inp

        if result_kind == RESULT_KIND_INTERNAL_ERROR:
            # Safe generic fallback — the LLM sees no raw stack trace.
            system = base_system + (
                "\n\nSITUACIÓN: Hubo un problema técnico temporal. "
                "Dile al cliente amablemente que intente de nuevo en un momento. 1 línea."
            )
            inp = f"Cliente dijo: {message_body}"
            return system, inp

        # Fallback — shouldn't normally happen
        system = base_system + "\n\nResponde amablemente al cliente."
        inp = f"Cliente dijo: {message_body}\nResumen pedido: {cart_summary_after}"
        return system, inp

    def execute(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_id: Optional[str] = None,
        session: Optional[Dict] = None,
    ) -> AgentOutput:
        """Planner (intent) -> executor (one tool) -> response generator from actual tool result and cart."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = business_context.get("business_id") if business_context else None

        if not business_id:
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, no pude identificar el negocio. Intenta de nuevo.",
                "state_update": {},
            }

        # Load session if not provided (e.g. when executor passes it)
        if session is None:
            from ..database.session_state_service import session_state_service
            load_result = session_state_service.load(wa_id, str(business_id))
            session = load_result.get("session", {})

        order_context = session.get("order_context") or {}
        order_state = order_context.get("state") or "GREETING"
        items = order_context.get("items") or []
        total = order_context.get("total") or 0
        if items:
            lines = []
            for it in items:
                notes_part = f" ({it['notes']})" if it.get("notes") else ""
                lines.append(f"{it.get('quantity', 0)}x {it.get('name', '')}{notes_part}")
            cart_summary_str = "; ".join(lines) + f". Subtotal: ${int(total):,}".replace(",", ".")
        else:
            cart_summary_str = "Pedido vacío."

        try:
            tracer.start_run(run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id))

            # 1) Planner: one intent + params
            planner_system = PLANNER_SYSTEM_TEMPLATE.format(
                order_state=order_state,
                cart_summary=cart_summary_str,
            )

            # Pending disambiguation: if last turn we offered the customer a set
            # of options, inject them so the planner can resolve replies like
            # "la normal", "la primera", "la Corona" to a specific product.
            pending = order_context.get("pending_disambiguation") or {}
            logging.warning(
                "[ORDER_AGENT] pending_disambiguation loaded=%s options=%s",
                bool(pending and pending.get("options")),
                [o.get("name") for o in (pending.get("options") or [])] if pending else [],
            )
            if pending and pending.get("options"):
                opts_lines = "\n".join(
                    f"  - {o.get('name')} (${int(o.get('price') or 0):,})".replace(",", ".")
                    for o in pending.get("options", [])
                )
                planner_system += (
                    "\n\nCONTEXTO DE ACLARACIÓN PENDIENTE: En tu turno ANTERIOR ofreciste al cliente "
                    f"estas opciones porque preguntó por \"{pending.get('requested_name', '')}\":\n"
                    f"{opts_lines}\n"
                    "Si el mensaje actual del cliente es una elección (ej. 'la normal', 'la primera', 'la barata', "
                    "'dame la Corona', 'la michelada', un nombre o un número), mapea su respuesta a UNA de estas opciones y "
                    "clasifícalo como ADD_TO_CART con product_name EXACTO de la opción elegida. "
                    "Ejemplos: 'la normal' → la opción SIN modificador (ej. \"Michelada\" no \"Corona michelada\"); "
                    "'la primera' → la primera de la lista; 'la más barata' → la de menor precio. "
                    "Si el cliente está cambiando de tema o pidiendo otra cosa, ignora este contexto y clasifica normalmente."
                )

            history_text = ""
            for msg in conversation_history[-6:]:
                role = msg.get("role", "")
                content = (msg.get("content") or msg.get("message", ""))[:200]
                history_text += f"{role}: {content}\n"
            planner_messages = [
                SystemMessage(content=planner_system),
                HumanMessage(content=f"Historial reciente:\n{history_text}\nUsuario: {message_body}\n\nResponde solo con JSON: intent y params."),
            ]
            planner_response = self.llm.invoke(planner_messages)
            planner_text = planner_response.content if hasattr(planner_response, "content") else str(planner_response)
            parsed = _parse_planner_response(planner_text)
            intent = (parsed.get("intent") or INTENT_CHAT).upper().replace(" ", "_")
            params = parsed.get("params") or {}
            logging.warning("[ORDER_AGENT] Planner intent=%s params=%s", intent, params)

            # 2) Executor: validate state, run one tool, update state
            exec_result = execute_order_intent(
                wa_id=wa_id,
                business_id=str(business_id),
                business_context=business_context,
                session=session,
                intent=intent,
                params=params,
            )
            success = exec_result.get("success", False)
            cart_summary_after = exec_result.get("cart_summary") or cart_summary_str

            # 3) Response: deterministic greeting for GREET, else LLM response generator
            if intent == INTENT_GREET:
                business_name = "BIELA FAST FOOD"
                menu_url = "https://gixlink.com/Biela"
                if business_context and business_context.get("business"):
                    biz = business_context["business"]
                    business_name = (biz.get("name") or business_name).strip()
                    settings = biz.get("settings") or {}
                    menu_url = (settings.get("menu_url") or menu_url).strip()

                customer_name = (name or "").strip()
                has_real_name = customer_name and customer_name.lower() not in ("usuario", "cliente", "user")

                # Preserve the existing greeting cases: personalized when we have a real name, generic otherwise.
                if has_real_name:
                    opener = f"Hola {customer_name}.\n\n"
                else:
                    opener = ""

                final_response_text = (
                    f"{opener}"
                    f"Gracias por comunicarte con {business_name}. ¿Cómo podemos ayudarte?\n\n"
                    "🍔🍟🔥😁\n\n"
                    "Recuerda que nuestro horario de atención al público es de 5:30 PM a 10:00 PM de lunes a viernes.\n\n"
                    f"{menu_url}"
                )
            else:
                result_kind = exec_result.get("result_kind", "")
                response_system, resp_input = self._build_response_prompt(
                    result_kind=result_kind,
                    exec_result=exec_result,
                    message_body=message_body,
                    business_context=business_context,
                    cart_summary_after=cart_summary_after,
                )
                response_messages = [
                    SystemMessage(content=response_system),
                    HumanMessage(content=resp_input + "\n\nResponde al cliente en español colombiano, breve y natural:"),
                ]
                response_llm = self.llm.invoke(response_messages)
                final_response_text = response_llm.content if hasattr(response_llm, "content") else str(response_llm)
                final_response_text = (final_response_text or "").strip() or "Listo. ¿En qué más puedo ayudarte?"

            conversation_service.store_conversation_message(wa_id, final_response_text, "assistant", business_id=business_id)

            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

            # state_update: if place_order cleared context, don't overwrite; else keep order agent active
            state_update = {"active_agents": ["order"]}
            if intent == "PLACE_ORDER" and success:
                state_update = {}

            return {
                "agent_type": self.agent_type,
                "message": final_response_text,
                "state_update": state_update,
            }

        except Exception as e:
            logging.exception("[ORDER_AGENT] Error: %s", e)
            tracer.end_run(run_id, success=False, error=str(e), latency_ms=(time.time() - start_time) * 1000)
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
