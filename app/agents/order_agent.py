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
    INTENT_ADD_TO_CART,
    INTENT_CHAT,
    INTENT_CONFIRM,
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
Productos YA en el pedido (NO los incluyas de nuevo en ADD_TO_CART a menos que el usuario pida explícitamente más cantidad con frases como "quiero otro", "dame uno más", "agrega otro". Si el usuario pide UN producto NUEVO, emite SOLO ese producto — no repitas los que ya están aquí): {cart_summary}

Intenciones válidas: GET_MENU_CATEGORIES, LIST_PRODUCTS, SEARCH_PRODUCTS, GET_PRODUCT, ADD_TO_CART, VIEW_CART, UPDATE_CART_ITEM, REMOVE_FROM_CART, PROCEED_TO_CHECKOUT, GET_CUSTOMER_INFO, SUBMIT_DELIVERY_INFO, PLACE_ORDER, CONFIRM, CHAT.

Nota: los saludos puros (sólo "hola", "buenas", "buen día") ya son manejados por el router antes de que llegues a clasificar — NO recibirás saludos puros.

Reglas de menú y búsqueda (importante):
- GET_MENU_CATEGORIES: cuando el usuario pregunta qué hay, qué tienes en general, o qué categorías hay (ej. "qué tienes", "qué hay en el menú"). Sin params.
- LIST_PRODUCTS con category: cuando pregunta qué tienes EN UNA CATEGORÍA (ej. "qué tienes de bebidas", "qué hamburguesas tienes", "qué bebidas hay"). Siempre pasa params: {{"category": "bebidas"}} o "hamburguesas", "BEBIDAS", etc. category vacío = menú completo. IMPORTANTE: pasa la categoría COMPLETA que el usuario mencionó, incluyendo calificadores de tipo como "de pollo", "de res", "de cerdo" (ej. "tienes hamburguesas de pollo?" → category="hamburguesas de pollo", NO solo "hamburguesas"). El backend normaliza la categoría automáticamente. Frases implícitas también cuentan — "qué hay para tomar", "qué tienen para tomar", "algo para beber", "qué tienen de beber" → LIST_PRODUCTS con category "bebidas". "qué hay para comer", "algo de comida" (sin más contexto) → LIST_PRODUCTS sin category (menú completo) o GET_MENU_CATEGORIES si prefiere ver categorías.
- SEARCH_PRODUCTS con query: cuando el usuario NOMBRA un producto o ingrediente o DESCRIBE lo que quiere (ej. "quiero barracuda", "tienes coca cola", "algo con queso azul", "algo picante", "algo con picante", "tienes algo dulce"). Incluye cualquier "algo con X", "algo X", "tienes algo X" — son búsquedas por atributo, NO preguntas por el menú general. No uses SEARCH_PRODUCTS para preguntas de categoría; para "qué tienes de X" usa LIST_PRODUCTS con category. IMPORTANTE: "tienes [nombre en plural de una categoría/tipo de comida]?" (ej. "tienes hamburguesas?", "tienes perros?", "tienes perros calientes?", "tienes bebidas?", "tienes cervezas?") es una pregunta por categoría, NO una búsqueda de producto — usa LIST_PRODUCTS con category, NO SEARCH_PRODUCTS ni GET_MENU_CATEGORIES. La intención del usuario es ver qué opciones hay en esa categoría. EXCEPCIÓN CLAVE: si la frase incluye un ADJETIVO CALIFICATIVO que describe una CUALIDAD (ej. "tienes hamburguesas picantes?", "algo dulce de bebida", "perros con queso", "hamburguesas grandes"), eso es una búsqueda por ATRIBUTO, no una categoría pura — usa SEARCH_PRODUCTS con query, NO LIST_PRODUCTS. El adjetivo (picante, dulce, grande, especial, etc.) indica que el usuario quiere filtrar por una característica, y LIST_PRODUCTS no soporta filtros de atributo. OJO: "de pollo", "de res", "de cerdo" NO son adjetivos — son especificadores de TIPO que forman subcategorías (ej. "hamburguesas de pollo" es una categoría, NO un atributo). Estos van a LIST_PRODUCTS con la frase completa como category.
- GET_PRODUCT con product_name: cuando pregunta qué trae o qué tiene UN producto específico en singular (ej. "qué trae la barracuda", "qué tiene la montesa").
- LIST_PRODUCTS (con la última categoría mostrada) cuando el usuario pide detalles de VARIOS/TODOS los productos ya listados — en plural o colectivo (ej. "qué tienen cada una", "qué trae cada una de esas hamburguesas", "dame los detalles de todas", "qué ingredientes tiene cada una"). NO uses GET_PRODUCT en estos casos: el usuario quiere ver todo el grupo, no uno solo.

Otras reglas:
- REGLA DE PRIORIDAD (más importante que las demás): si el mensaje NOMBRA uno o más productos del menú (aunque esté acompañado de un saludo, de la palabra "domicilio", "pedido", "por favor", o de una lista con saltos de línea), SIEMPRE clasifica como ADD_TO_CART con los items correspondientes. El saludo y palabras como "domicilio"/"pedido" son contexto, NO intención — se ignoran para la clasificación cuando hay productos nombrados. CHAT se usa SOLO cuando NO hay ningún producto en el mensaje.
- REFERENCIA PRONOMINAL A PRODUCTO RECIENTE: si el mensaje usa un pronombre demostrativo ("esa", "ese", "eso", "esas", "esos", "la misma", "el mismo", "deme esa", "quiero ese", "dame eso") para referirse a un producto que se acaba de mencionar o mostrar en la conversación, trátalo como si el usuario NOMBRÓ ese producto. Revisa el historial reciente para identificar cuál producto se mencionó. Si además incluye modificaciones ("sin X", "con extra Y", "pero sin salsa"), clasifica como ADD_TO_CART con product_name del producto referenciado y notes con la modificación. Ejemplo: bot acaba de mostrar "AL PASTOR" y usuario dice "deme esa pero sin salsa picante" → {{"intent": "ADD_TO_CART", "params": {{"product_name": "AL PASTOR", "quantity": 1, "notes": "sin salsa picante"}}}}. Esta regla tiene PRIORIDAD sobre las reglas de MODIFICACIONES y AÑADIR nota de más abajo — esas aplican SOLO cuando el producto YA ESTÁ en el carrito.
- Si el usuario expresa intención de pedir u ordenar SIN nombrar ningún producto específico (ej. "para un domicilio", "quiero pedir", "quiero hacer un pedido", "buenas, un domicilio por favor", "me pueden atender"): usa CHAT. El usuario probablemente ya sabe qué quiere; solo invítalo a decir su pedido. NO uses ADD_TO_CART, SEARCH_PRODUCTS ni GET_MENU_CATEGORIES porque no hay producto ni pregunta por el menú. IMPORTANTE: esta regla aplica SOLO si no hay productos nombrados — si hay aunque sea un producto, gana la regla de prioridad de arriba.
- Si pide agregar uno o más productos: ADD_TO_CART. Para un solo producto: params con "product_name" (o "product_id"), "quantity" y opcionalmente "notes" para instrucciones especiales (ej. "sin cebolla", "sin morcilla", "extra salsa"). REGLA IMPORTANTE PARA BEBIDAS GENÉRICAS: si el usuario pide un producto genérico con un sabor o fruta (ej. "un jugo de mora en agua", "un jugo de mango en leche", "un hervido de maracuyá"), pasa la frase COMPLETA como product_name incluyendo el sabor — NO la simplifiques al nombre del catálogo. Ejemplo: "un jugo de mango en agua" → product_name="jugo de mango en agua" (NO "Jugos en agua"). Ejemplo: "un jugo de fresa en leche" → product_name="jugo de fresa en leche". El backend extrae el sabor automáticamente. Para varios productos: params con "items": [ {{"product_name": "NOMBRE", "quantity": 1, "notes": "..."}}, ... ]. Ejemplo con nota: "una barracuda sin cebolla caramelizada" → {{"intent": "ADD_TO_CART", "params": {{"product_name": "BARRACUDA", "quantity": 1, "notes": "sin cebolla caramelizada"}}}}. Ejemplo varios: "dame una montesa y una booster" → {{"intent": "ADD_TO_CART", "params": {{"items": [{{"product_name": "MONTESA", "quantity": 1}}, {{"product_name": "BOOSTER", "quantity": 1}}]}}}}. Ejemplo saludo + pedido multi-producto: "hola buenas un domicilio por favor, 2 betas, 1 barracuda, 1 biela fries" → {{"intent": "ADD_TO_CART", "params": {{"items": [{{"product_name": "BETA", "quantity": 2}}, {{"product_name": "BARRACUDA", "quantity": 1}}, {{"product_name": "BIELA FRIES", "quantity": 1}}]}}}}. Ejemplo con saltos de línea: "hola buenas tardes un domicilio por favor\\n2 betas\\n1 barracuda\\n1 biela fries" → mismo resultado (los saltos de línea son solo formato).
- MODIFICACIONES DE INGREDIENTES en producto YA AGREGADO al pedido (ej. "sin morcilla", "para que no le pongan cebolla", "quítale el queso"): usa UPDATE_CART_ITEM con "product_name" del producto en el pedido y "notes" con la instrucción. Ejemplo: pedido tiene PICADA y usuario dice "para que no le pongan morcilla" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "PICADA", "notes": "sin morcilla"}}}}. NUNCA uses ADD_TO_CART para modificar un ingrediente de un producto existente. IMPORTANTE: esta regla aplica SOLO si el producto ya aparece en "Productos YA en el pedido" de arriba. Si el carrito está vacío o el producto NO está en el carrito, NO uses UPDATE_CART_ITEM — usa ADD_TO_CART con notes (o la regla de REFERENCIA PRONOMINAL si el usuario usa "esa"/"ese").
- AÑADIR una nota / sabor / detalle a un producto YA en el pedido (ej. "el jugo también es de mora", "el jugo en agua es de lulo", "al jugo en leche agrégale mango", "hazlo de mango"): usa UPDATE_CART_ITEM con "product_name" del producto ACTUAL en el carrito y "notes" igual al nuevo detalle (ej. "mora", "lulo", "mango"). NO lo confundas con un nuevo pedido — el cliente está describiendo el producto existente, no ordenando otro. Ejemplo: carrito tiene 'Jugos en agua' y cliente dice "el jugo en agua también es de mora" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "Jugos en agua", "notes": "mora"}}}}. IMPORTANTE: esta regla aplica SOLO si el producto ya aparece en el carrito. Si no está en el carrito, NO uses UPDATE_CART_ITEM.
- REEMPLAZO POR VARIANTE / SABOR / TIPO de un producto YA en el pedido (ej. "la soda que sea de frutos rojos", "mejor la hamburguesa doble", "cámbiala por la de pollo", "que sea la Corona", "la cerveza que sea Poker"): usa UPDATE_CART_ITEM con "product_name" = nombre del producto ACTUAL en el carrito, y "new_product_name" = nombre completo del producto NUEVO combinando el nombre actual con la variante. Ejemplo: carrito tiene "Soda" y usuario dice "la soda que sea de frutos rojos" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "Soda", "new_product_name": "Soda Frutos rojos"}}}}. Ejemplo: carrito tiene "Michelada" y usuario dice "que sea con Corona" → {{"intent": "UPDATE_CART_ITEM", "params": {{"product_name": "Michelada", "new_product_name": "Corona michelada"}}}}. Distingue de `notes`: usa `notes` SOLO para exclusiones/añadidos de ingredientes (ej. "sin morcilla", "extra salsa"), NUNCA para elegir otra variante del producto. NUNCA uses ADD_TO_CART para un reemplazo: UPDATE_CART_ITEM con new_product_name maneja la sustitución atómica.
- Si pide quitar un producto del pedido completamente ("elimina la malteada", "quita eso", "no quiero la coca cola"): REMOVE_FROM_CART con "product_name". Usa SOLO el nombre BASE del producto, SIN las notas entre paréntesis. Ejemplo: el pedido tiene "Jugos en leche (mango)" → product_name="Jugos en leche" (NO "Jugos en leche (mango)"). Otro ejemplo: "elimina la malteada" → {{"intent": "REMOVE_FROM_CART", "params": {{"product_name": "malteada"}}}}.
- Si pregunta por el ESTADO DE SU PEDIDO — no por el menú — usa VIEW_CART sin params. Frases típicas: "¿qué tengo en mi pedido?", "¿qué hay en mi pedido?", "cómo va mi pedido", "mi orden", "qué llevo", "qué he pedido", "muéstrame mi pedido", "ver mi pedido", "mi carrito", "cómo quedó mi pedido". NO confundas con GET_MENU_CATEGORIES / LIST_PRODUCTS — esos son para preguntar qué tiene el restaurante, VIEW_CART es para revisar lo que el cliente ya agregó al pedido.
- CONFIRMACIÓN (regla única, muy importante): si el mensaje del usuario es una confirmación pura — "listo", "procedamos", "procedemos", "confirmar", "confirmo", "dale", "sí", "si", "ok", "okay", "perfecto", "ya", "vale", "de acuerdo" — SIN nombrar producto, dirección, teléfono ni medio de pago, usa SIEMPRE `{{"intent": "CONFIRM", "params": {{}}}}`. TAMBIÉN cuenta como CONFIRM cuando el ÚLTIMO mensaje del bot preguntó si desea agregar algo más o si procedemos (ej. "¿algo más?", "¿quieres agregar algo más?", "¿procedemos?", "¿procedemos con el pedido?") y el usuario responde con una NEGACIÓN que significa "no quiero nada más, procede" — "no", "que no", "nada", "nada más", "eso es todo", "no más", "así está bien", "ya no", "estamos bien", "no gracias", "así déjalo", "con eso". IMPORTANTE: estas negaciones SOLO son CONFIRM cuando el bot ofreció agregar más o preguntó si procede; si el bot hizo otra pregunta de sí/no (ej. "¿quieres la hamburguesa?", "¿te la cambio?"), una negación NO es CONFIRM sino CHAT. No decidas tú si significa "proceder al checkout" o "colocar el pedido": el backend lo resuelve según el estado actual. NUNCA uses PROCEED_TO_CHECKOUT ni PLACE_ORDER directamente para palabras de confirmación. Si además de confirmar el usuario provee datos de entrega (ej. "listo, mi dirección es calle 1"), usa SUBMIT_DELIVERY_INFO con los datos, no CONFIRM.
- Si ya están en recolección de datos (COLLECTING_DELIVERY) y el usuario PROVEE datos: usa SUBMIT_DELIVERY_INFO con uno o más de: address, phone, name, payment_method; params pueden ser parciales, ej. {{"address": "Calle 1"}}, {{"payment_method": "Efectivo"}}, {{"name": "Juan", "phone": "+57..."}}. Si solo necesitas saber qué falta (sin que el usuario haya dado nada nuevo), usa GET_CUSTOMER_INFO.
- Si el usuario corrige dirección, teléfono o medio de pago (ej. "no es esa dirección, es calle X", "mejor a esta dirección", "el teléfono es otro"): usa SUBMIT_DELIVERY_INFO con el valor nuevo, ej. {{"address": "calle 19#29-99"}}.
- Si el usuario indica que su teléfono es el MISMO desde el que está escribiendo (ej. "este número", "este mismo", "el mismo", "mi whatsapp", "con este mismo", "el de whatsapp", "al que te estoy escribiendo"): usa SUBMIT_DELIVERY_INFO con `phone` igual al marcador literal `<SENDER>`. Ejemplo: {{"intent": "SUBMIT_DELIVERY_INFO", "params": {{"phone": "<SENDER>"}}}}. El backend sustituirá el marcador por el número real del remitente. NUNCA inventes un número.
- RESOLUCIÓN DE NOMBRES ABREVIADOS: si el historial reciente muestra que el bot acaba de listar productos (ej. "Tenemos: DENVER, NAIROBI, PEGORETTI, SPECIAL DOG") y el usuario responde con un nombre corto o abreviado que coincide con UNO de esos productos (ej. "un special"), usa el nombre COMPLETO del catálogo tal como apareció en la lista (ej. "SPECIAL DOG"). NO envíes solo la parte que el usuario dijo — la búsqueda del backend puede encontrar múltiples productos con nombres similares (ej. "SPECIAL DOG" y "SPECIAL FRIES") y desambiguar innecesariamente. Ejemplos: bot listó "SPECIAL DOG, PEGORETTI, NAIROBI, DENVER" → usuario dice "un special" → product_name="SPECIAL DOG". Bot listó "BIELA FRIES, CHEESE FRIES" → usuario dice "las biela" → product_name="BIELA FRIES".
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
- Listado de productos: cuando el resultado incluya MÚLTIPLES productos (el usuario preguntó por una categoría, tipo de comida, o "tienes X?"), lista TODOS los productos que aparecen en el resultado, no solo el primero o uno destacado. El usuario quiere ver sus opciones. Si hay más de 5 productos, lista los primeros 5 con nombre, precio y descripción breve, y al final di cuántos más hay e invita al usuario a preguntar por más opciones o visitar el menú.
- Datos de entrega: NUNCA digas "Tengo esta dirección, teléfono y tipo de pago" a menos que el resultado de la herramienta contenga exactamente "DELIVERY_STATUS" y "all_present=true". Si el resultado es "OK_COLLECTING_DELIVERY" (sin DELIVERY_STATUS), responde pidiendo los datos: "Para continuar con tu pedido necesito: nombre, dirección, teléfono y medio de pago. ¿Me los indicas?". Si el resultado tiene DELIVERY_STATUS y all_present=true, confirma incluyendo los valores reales (dirección, teléfono, medio de pago) en el mensaje: "Tengo esta dirección: [valor], teléfono [valor] y pago [valor]. ¿Gustas proceder o quieres enviarla a otra dirección?". Si DELIVERY_STATUS tiene missing= o all_present=false: pide SOLO lo que falta (ej. "Me falta: teléfono y medio de pago. ¿Me los indicas?") o todo si faltan todos; NUNCA en ese caso sugieras "proceder con el pedido" ni "agregar algo más" hasta que todos los datos estén completos.
- Ubicación y datos del negocio: si el usuario pregunta dónde estamos ubicados, horarios, teléfono de contacto o dirección del local, responde usando ÚNICAMENTE la "Información del negocio" que te doy a continuación. Si esa información está vacía o dice "no configurada", di que por el momento no tienes esa información a mano y que puede preguntar por el menú o hacer su pedido.
- Combos / hamburguesas con papas: si el usuario pregunta si tienen combos, si las hamburguesas vienen con papas o si incluyen papas, responde SIEMPRE usando la sección "Reglas y contexto del negocio" de la Información del negocio (aunque la intención ejecutada haya sido GET_MENU_CATEGORIES o GET_PRODUCT). No digas "no encontré información" ni solo listes categorías; da la respuesta de las reglas (ej. todas las hamburguesas vienen con papas, bebida aparte).
- Estado del pedido / entrega: si el usuario pregunta por tiempos de entrega, seguimiento o estado de envío (ej. "¿a qué hora llega?", "¿ya salió mi pedido?", "¿cuánto se demora?") y NO hay un pedido confirmado (el resumen del pedido dice "Pedido vacío" o muestra productos SIN que se haya colocado la orden), NUNCA digas que el pedido está en camino ni inventes un estado de entrega. Responde que aún no tiene un pedido confirmado y ofrece ayuda para completar o hacer su pedido.
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


def build_pending_disambiguation_prompt_block(pending: Optional[Dict[str, Any]]) -> str:
    """
    Build the "CONTEXTO DE ACLARACIÓN PENDIENTE" block appended to the
    planner system prompt when a pending disambiguation is active.

    Extracted from OrderAgent.execute so integration tests (and any
    future caller) use exactly the same text — otherwise the test
    helper's hand-rolled copy drifts from production.

    Returns an empty string when there's no active pending
    disambiguation or no options to offer.
    """
    if not pending or not pending.get("options"):
        return ""
    opts_lines = "\n".join(
        f"  - {o.get('name')} (${int(o.get('price') or 0):,})".replace(",", ".")
        for o in pending.get("options", [])
    )
    return (
        "\n\nCONTEXTO DE ACLARACIÓN PENDIENTE: En tu turno ANTERIOR ofreciste al cliente "
        f"estas opciones porque preguntó por \"{pending.get('requested_name', '')}\":\n"
        f"{opts_lines}\n"
        "\n"
        "REGLA 1 — MAPEO A OPCIÓN: si el mensaje actual del cliente es una elección "
        "(ej. 'la normal', 'la primera', 'la barata', 'dame la Corona', 'la michelada', "
        "'un jugo en agua', 'el de leche', un nombre o un número), mapea su respuesta "
        "a UNA de estas opciones y clasifícalo como ADD_TO_CART con product_name EXACTO "
        "de la opción elegida. Ejemplos: 'la normal' → la opción SIN modificador "
        "(ej. \"Michelada\" no \"Corona michelada\"); 'la primera' → la primera de la "
        "lista; 'la más barata' → la de menor precio.\n"
        "\n"
        "REGLA 2 — PRESERVAR SABOR / INGREDIENTE / DETALLE (MUY IMPORTANTE): si además "
        "del mapeo de REGLA 1 el cliente menciona un sabor, fruta, ingrediente, color, "
        "tamaño, o cualquier calificador que NO forma parte del nombre de la opción "
        "elegida, incluye ese calificador en el campo `notes` del ADD_TO_CART. "
        "NUNCA lo borres. Aplica incluso si el nombre de la opción parece cubrir "
        "todos los sabores (ej. 'Jugos en agua' es una fila genérica del catálogo; "
        "el sabor va como nota).\n"
        "\n"
        "Ejemplos obligatorios de REGLA 2 (úsalos como referencia exacta):\n"
        "  • Opciones: Jugos en agua, Jugos en leche, Hervido Mora\n"
        "    Cliente dice: 'un jugo de mora en agua'\n"
        "    → {\"intent\":\"ADD_TO_CART\",\"params\":{\"product_name\":\"Jugos en agua\",\"notes\":\"mora\"}}\n"
        "  • Opciones: Jugos en agua, Jugos en leche\n"
        "    Cliente dice: 'dame uno de mango en leche'\n"
        "    → {\"intent\":\"ADD_TO_CART\",\"params\":{\"product_name\":\"Jugos en leche\",\"notes\":\"mango\"}}\n"
        "  • Opciones: Jugos en agua, Jugos en leche\n"
        "    Cliente dice: 'el de lulo en agua por favor'\n"
        "    → {\"intent\":\"ADD_TO_CART\",\"params\":{\"product_name\":\"Jugos en agua\",\"notes\":\"lulo\"}}\n"
        "\n"
        "Contraejemplos (REGLA 2 NO aplica):\n"
        "  • Cliente dice 'el de agua' → notes vacío (no hay sabor mencionado).\n"
        "  • Cliente dice 'la michelada' → notes vacío (es solo el mapeo, sin detalle).\n"
        "\n"
        "Si el cliente está cambiando de tema o pidiendo algo completamente distinto "
        "a las opciones (ej. ahora quiere una hamburguesa, o pide el menú), ignora "
        "este contexto y clasifica normalmente."
    )


def apply_disamb_reply_flavor_fallback(
    parsed: Dict[str, Any],
    message_body: str,
    pending: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Deterministic safety net on the disambiguation-reply planner path.

    The planner prompt (REGLA 2 in the pending-context block) asks the
    LLM to carry qualifier tokens from the user's reply over into
    ``params["notes"]`` when the chosen product is an exact option name.
    gpt-4o-mini sometimes complies and sometimes strips the qualifier.
    This function patches the latter case in pure Python:

      1. Only runs when there's an active pending disambiguation.
      2. Only runs on ``ADD_TO_CART`` with a single ``product_name``
         (multi-item batches stay as-is).
      3. Only runs when ``notes`` is missing / empty.
      4. Only runs when the chosen ``product_name`` normalizes to one
         of the pending option names (so we don't inject notes on a
         "topic change" reply where the user genuinely wants a
         different product).
      5. Computes the set of stemmed user-message tokens NOT present
         in the chosen product name and, if non-empty, joins them in
         original-surface order and writes them to ``params["notes"]``.

    Returns the (possibly mutated) parsed dict. Modifies in place too.
    """
    if not pending or not pending.get("options"):
        return parsed
    params = parsed.get("params") or {}
    parsed["params"] = params
    if (parsed.get("intent") or "").upper() != INTENT_ADD_TO_CART:
        return parsed
    chosen = params.get("product_name")
    if not isinstance(chosen, str) or not chosen.strip():
        return parsed
    if (params.get("notes") or "").strip():
        return parsed

    from ..services import product_search as _ps
    chosen_norm = _ps._normalize(chosen.strip())
    option_norms = {
        _ps._normalize(o.get("name", "") or "")
        for o in pending.get("options", [])
    }
    if chosen_norm not in option_norms:
        return parsed

    chosen_stems = {
        _ps._stem(t) for t in _ps._tokenize(chosen_norm) if t
    }
    user_tokens = _ps._tokenize(_ps._normalize(message_body or ""))
    leftover: List[str] = []
    seen_stems = set()
    for tok in user_tokens:
        st = _ps._stem(tok) or tok
        if st in chosen_stems or st in seen_stems:
            continue
        seen_stems.add(st)
        leftover.append(tok)
    if leftover:
        injected = " ".join(leftover)
        params["notes"] = injected
        logging.warning(
            "[ORDER_AGENT] flavor preservation: injected notes=%r "
            "for chosen=%r (planner stripped qualifier)",
            injected, chosen,
        )
    return parsed


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
                "- PROHIBIDO ABSOLUTO: inventar, traducir, especializar, o combinar los nombres de "
                "la lista con lo que el cliente pidió. Por ejemplo, si la lista dice 'Jugos en leche' "
                "y el cliente pidió 'jugo de mora en leche', la opción que muestras se llama "
                "'Jugos en leche' — NUNCA la llames 'Jugo de Mora en Leche' ni similar. Copia los "
                "nombres exactamente como aparecen en la lista, con mayúsculas y acentos idénticos.\n"
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
            menu_url = ""
            if business_context and business_context.get("business"):
                settings = business_context["business"].get("settings") or {}
                if isinstance(settings, dict):
                    menu_url = (settings.get("menu_url") or "").strip()

            rules = (
                "\n\nSITUACIÓN: El cliente preguntó por el menú. Tienes las categorías disponibles. "
                "REGLAS:\n"
                "- Presenta las categorías de forma amigable (puedes traducirlas al español natural, ej. HAMBURGUESAS → hamburguesas).\n"
                "- NO listes productos individuales — solo categorías.\n"
                "- Termina invitando al cliente a elegir una categoría o a pedir si ya sabe qué quiere.\n"
                "- Tono cálido, no robótico. Máx 5-6 líneas."
            )
            if menu_url:
                rules += (
                    "\n- MENU URL disponible: úsalo SIEMPRE en la respuesta.\n"
                    "- Si el cliente pidió explícitamente que LE ENVÍES la carta/menú/link "
                    "(verbos: 'envías', 'mandas', 'pasas', 'compartes', 'me puedes enviar', "
                    "'mándame', 'envíame', 'pásame', 'dame'; o sustantivos: 'el link', 'la carta' "
                    "como objeto directo), LIDERA con el URL en su propia línea, luego menciona "
                    "las categorías brevemente como referencia.\n"
                    "- Si el cliente solo preguntó qué hay (ej. 'qué tienen', 'qué hay en el menú'), "
                    "LIDERA con las categorías y pon el URL al final como oferta suave "
                    "(ej. 'si quieres ver la carta completa: <url>')."
                )
            system = base_system + rules

            inp_parts = [
                f"Cliente dijo: {message_body}",
                f"Categorías disponibles:\n{cats_lines}",
            ]
            if menu_url:
                inp_parts.append(f"URL de la carta: {menu_url}")
            inp = "\n".join(inp_parts)
            return system, inp

        if result_kind == RESULT_KIND_PRODUCTS_LIST:
            products = exec_result.get("products") or []
            query_label = exec_result.get("query_label")
            category_label = exec_result.get("category_label")
            if not products:
                label = query_label or category_label or "eso"
                system = base_system + (
                    "\n\nSITUACIÓN: No hay productos que coincidan con lo que el cliente pidió. "
                    "REGLAS CRÍTICAS:\n"
                    "- Di explícitamente que NO tenemos ese producto/categoría (ej. \"no tenemos pizza\").\n"
                    "- NUNCA ofrezcas productos como si fueran una versión del producto pedido. No digas \"tenemos esta pizza\" si no tienes pizzas — nunca.\n"
                    "- Puedes invitar amablemente a ver las categorías disponibles del menú.\n"
                    "- Sin 'lo siento', 'disculpa', ni 'error'. 1-2 líneas."
                )
                inp = f"Cliente buscó: {label}\nNo hay coincidencias exactas ni parciales en el catálogo."
                return system, inp
            # Defense in depth: even after the Phase 2 filter, if every item
            # only matched via embedding the response generator should not
            # pretend they exactly match the query. Flag it so the prompt
            # phrases them as "related" rather than authoritative.
            all_embedding = bool(products) and all(
                (p.get("matched_by") == "embedding") for p in products
            )
            # Cap at 5 products for WhatsApp readability. Keep total count
            # so the response generator can say "y X más".
            _MAX_LISTED = 5
            total_count = len(products)
            products_shown = products[:_MAX_LISTED]
            remaining = total_count - len(products_shown)

            prods_lines = "\n".join(
                f"- {p.get('name')} ({money(p.get('price'))})"
                + (f" — {p.get('description')}" if p.get("description") else "")
                for p in products_shown
            )
            context_label = ""
            if category_label:
                context_label = f"Categoría: {category_label}"
            elif query_label:
                context_label = f"Búsqueda: {query_label}"
            remaining_note = ""
            if remaining > 0:
                remaining_note = (
                    f"\n(Mostrando {len(products_shown)} de {total_count} productos. "
                    f"Hay {remaining} más disponibles.)"
                )
            rules = (
                "\n\nSITUACIÓN: El cliente pidió ver una lista de productos. "
                "REGLAS:\n"
                "- Presenta los productos de forma clara con nombre y precio.\n"
                "- Si el producto trae descripción en los datos, INCLÚYELA SIEMPRE (1 línea breve por producto). Nunca omitas descripciones aunque la lista sea larga.\n"
                "- Si NO hay descripciones disponibles, lista solo nombre y precio.\n"
                "- Si hay descripciones y el cliente preguntó por un ingrediente, menciona primero el producto cuya descripción coincide.\n"
                "- Si hay más productos de los que se muestran, menciona cuántos más hay al final e invita al usuario a preguntar por más opciones o visitar el menú.\n"
                "- Termina invitando a ordenar o a preguntar por alguno en particular.\n"
                "- NUNCA muestres IDs ni códigos internos."
            )
            if all_embedding and query_label:
                rules += (
                    "\n- IMPORTANTE: ninguno de estos productos coincide exactamente con "
                    f"lo que el cliente buscó ({query_label}). Presenta la lista como "
                    "\"opciones relacionadas que podrían gustarte\", NO como \"aquí están "
                    f"los {query_label}\". Nunca afirmes que la lista ES {query_label}."
                )
            system = base_system + rules
            inp = f"Cliente dijo: {message_body}\n{context_label}\nProductos disponibles:\n{prods_lines}{remaining_note}"
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

            pending_clarification = exec_result.get("pending_clarification") or {}
            pending_options = pending_clarification.get("options") or []
            pending_requested = pending_clarification.get("requested_name") or ""
            not_found_items = [x for x in (exec_result.get("not_found") or []) if x]

            not_found_block = ""
            if not_found_items:
                not_found_block = (
                    "Productos que NO encontramos en el menú (menciónalos al cliente de "
                    "forma breve y ofrece otra opción o ver el menú):\n"
                    + "\n".join(f"- {n}" for n in not_found_items)
                )

            if pending_options:
                # Multi-item batch: some items were added, but one item
                # in the same message was ambiguous. The response must
                # confirm the partial success AND ask the open question,
                # using the exact option names from the catalog (no
                # hallucinated names).
                opts_lines = "\n".join(
                    f"- {o.get('name')} ({money(o.get('price'))})" for o in pending_options
                )
                not_found_rule = ""
                if not_found_items:
                    not_found_rule = (
                        "- Si hay productos listados como NO encontrados, menciónalos en "
                        "una sola frase breve ('X no está en el menú por ahora') antes o "
                        "después de la lista de opciones.\n"
                    )
                system = base_system + (
                    f"\n\nSITUACIÓN: {situation} PERO uno de los productos que el cliente pidió "
                    "tiene varias variantes y necesitas que elija cuál quiere antes de agregarlo.\n"
                    "REGLAS:\n"
                    "- Primero confirma brevemente lo que SÍ se agregó (está en 'Cambio realizado').\n"
                    "- Luego, en la misma respuesta, pregunta por el producto que quedó pendiente "
                    f"(lo pidió como '{pending_requested or 'ese producto'}') y lista las opciones.\n"
                    + not_found_rule +
                    "- PROHIBIDO ABSOLUTO: inventar, traducir, especializar o combinar los nombres "
                    "de las opciones con lo que el cliente pidió. Copia los nombres de las opciones "
                    "EXACTAMENTE como aparecen (mayúsculas y acentos idénticos).\n"
                    "- No digas 'error', 'no pude', 'lo siento' — es una pregunta normal.\n"
                    "- NO sugieras proceder con el pedido todavía: falta resolver la duda.\n"
                    "- 3-6 líneas total."
                )
                inp_parts = [
                    f"Cliente dijo: {message_body}",
                    f"Cambio realizado:\n{change_desc}",
                    f"Pedido actual:\n{cart_lines}",
                    f"Subtotal: {money(total_after)}",
                    f"Producto pendiente de aclarar: {pending_requested}",
                    f"Opciones disponibles (usa exactamente estos nombres y precios):\n{opts_lines}",
                ]
                if not_found_block:
                    inp_parts.append(not_found_block)
                return system, "\n".join(inp_parts)

            if not_found_items:
                # Multi-item batch: some items were added, others weren't
                # found at all (typo, truly unavailable). Confirm the
                # successes AND flag the missing items — never drop them
                # silently, and never present them as suggestions.
                missing_list = ", ".join(f"'{m}'" for m in not_found_items)
                system = base_system + (
                    f"\n\nSITUACIÓN: {situation} PERO uno o más productos que el cliente "
                    f"pidió no existen en nuestro menú (lista literal: {missing_list}). "
                    "No pudimos agregarlos al pedido. Estos NO son sugerencias — son "
                    "productos que el restaurante no vende.\n"
                    "REGLAS CRÍTICAS:\n"
                    "- Primero confirma lo que SÍ se agregó (está en 'Cambio realizado'), "
                    "usando los nombres EXACTOS de 'Pedido actual'.\n"
                    "- Luego, en una frase, di CLARAMENTE que los productos listados como "
                    "'no encontrados' NO están en el menú. Usa lenguaje explícito: "
                    "\"no tenemos [X] en el menú\", \"[X] no está disponible hoy\", "
                    "\"[X] no lo manejamos\". NO los presentes como sugerencias para que "
                    "el cliente los pida después.\n"
                    "- PROHIBIDO: frases como \"¿quieres agregar [X]?\", "
                    "\"¿te gustaría [X]?\", \"como un [X]\", que hacen parecer que el "
                    "producto no encontrado es una opción disponible.\n"
                    "- PROHIBIDO: 'lo siento', 'disculpa', 'no pude', 'falló', 'error'.\n"
                    "- Puedes ofrecerle al cliente ver el menú o pedir algo diferente "
                    "para los ítems faltantes — pero DESPUÉS de decir claramente que no "
                    "están disponibles.\n"
                    "- NO sugieras proceder con el pedido todavía si el cliente quería "
                    "esos productos faltantes.\n"
                    "- 2-5 líneas total."
                )
                inp = (
                    f"Cliente dijo: {message_body}\n"
                    f"Cambio realizado:\n{change_desc}\n"
                    f"Pedido actual:\n{cart_lines}\n"
                    f"Subtotal: {money(total_after)}\n"
                    f"{not_found_block}"
                )
                return system, inp

            system = base_system + (
                f"\n\nSITUACIÓN: {situation}\n"
                "REGLAS:\n"
                "- Confirma brevemente el cambio que hizo el backend (está en 'Cambio realizado').\n"
                "- Muestra el resumen del pedido actual usando los nombres EXACTOS de la lista "
                "'Pedido actual' que te doy. Copia el nombre del producto tal cual aparece "
                "(con mayúsculas, acentos y notas entre paréntesis). NO los reemplaces por las "
                "palabras que usó el cliente en su mensaje.\n"
                "- Sugiere el siguiente paso: preguntar si quiere agregar algo más (ej. bebida si no tiene una) o procedemos con el pedido.\n"
                "- NO inventes productos, nombres, variantes, ni precios — usa solo los datos dados.\n"
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
                "- Indica que el pedido se demora entre 40 a 50 minutos en su entrega.\n"
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
        stale_turn: bool = False,
        abort_key: Optional[str] = None,
        **kwargs,
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

        # Load session if not provided (e.g. when executor passes it).
        # Goes through the turn cache so the rest of the flow reuses
        # the same load result.
        if session is None:
            from ..orchestration import turn_cache
            from ..database.session_state_service import session_state_service
            load_result = turn_cache.current().get_session(
                wa_id, str(business_id),
                loader=lambda: session_state_service.load(wa_id, str(business_id)),
            )
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
            planner_system += build_pending_disambiguation_prompt_block(pending)

            if stale_turn:
                planner_system += (
                    "\n\nNOTA: El usuario envió este mensaje ANTES de ver tu respuesta "
                    "anterior. No asumas que leyó o reaccionó a tu último mensaje."
                )

            history_text = ""
            for msg in conversation_history[-6:]:
                role = msg.get("role", "")
                content = (msg.get("content") or msg.get("message", ""))[:400]
                history_text += f"{role}: {content}\n"
            planner_messages = [
                SystemMessage(content=planner_system),
                HumanMessage(content=f"Historial reciente:\n{history_text}\nUsuario: {message_body}\n\nResponde solo con JSON: intent y params."),
            ]
            planner_response = self.llm.invoke(
                planner_messages,
                config={
                    "run_name": "order_planner",
                    "metadata": {
                        "wa_id": wa_id,
                        "business_id": str(business_id),
                        "order_state": order_state,
                        "stale_turn": stale_turn,
                        "run_id": run_id,
                    },
                },
            )
            planner_text = planner_response.content if hasattr(planner_response, "content") else str(planner_response)
            parsed = _parse_planner_response(planner_text)
            # Deterministic safety net: if the planner stripped a
            # qualifier token (e.g. "mora") from a disamb reply that
            # mapped to an exact option name, re-attach it as notes.
            # See apply_disamb_reply_flavor_fallback for the full
            # contract.
            parsed = apply_disamb_reply_flavor_fallback(parsed, message_body, pending)
            intent = (parsed.get("intent") or INTENT_CHAT).upper().replace(" ", "_")
            params = parsed.get("params") or {}
            logging.warning("[ORDER_AGENT] Planner intent=%s params=%s", intent, params)

            # ── Abort check: a newer message arrived while the planner ran.
            # Skip executor + response so cart state stays clean. Requeue
            # the aborted text into the debounce buffer so the next flusher
            # coalesces it with newer arrivals — the planner then sees the
            # full thread instead of only the trailing message. Scales to N
            # consecutive aborts because requeue appends to the same buffer.
            if abort_key:
                from ..services.debounce import check_abort, clear_abort, requeue_aborted_text
                if check_abort(abort_key):
                    clear_abort(abort_key)
                    requeue_aborted_text(abort_key, message_body)
                    logging.warning(
                        "[ABORT] %s: aborting after planner (intent=%s) — requeued for next flush",
                        wa_id, intent,
                    )
                    tracer.end_run(run_id, success=False, latency_ms=(time.time() - start_time) * 1000)
                    return {
                        "agent_type": self.agent_type,
                        "message": "__ABORTED__",
                        "state_update": {},
                    }

            # 2) Executor: validate state, run one tool, update state
            exec_result = execute_order_intent(
                wa_id=wa_id,
                business_id=str(business_id),
                business_context=business_context,
                session=session,
                intent=intent,
                params=params,
                conversation_history=conversation_history,
            )
            success = exec_result.get("success", False)
            cart_summary_after = exec_result.get("cart_summary") or cart_summary_str

            # One structured event per turn — grep [ORDER_TURN] to reconstruct a
            # user's session, or alert on rejected=true (planner drift signal).
            state_after = exec_result.get("state_after") or order_state
            result_kind_tag = exec_result.get("result_kind") or ""
            planner_intent_rejected = (
                result_kind_tag == RESULT_KIND_USER_ERROR
                and (exec_result.get("error_kind") == "user_visible")
            )
            logging.warning(
                "[ORDER_TURN] wa_id=%s turn_id=%s state_in=%s intent=%s "
                "result_kind=%s state_out=%s success=%s rejected=%s "
                "latency_ms=%d",
                wa_id,
                message_id or "-",
                order_state,
                intent,
                result_kind_tag,
                state_after,
                success,
                planner_intent_rejected,
                int((time.time() - start_time) * 1000),
            )

            # 3) Response generator.
            # Pure greetings are handled upstream by the router fast-path
            # (see app/services/business_greeting.py) and never reach this agent.
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
            response_llm = self.llm.invoke(
                response_messages,
                config={
                    "run_name": "order_response",
                    "metadata": {
                        "wa_id": wa_id,
                        "business_id": str(business_id),
                        "intent": intent,
                        "result_kind": result_kind,
                        "run_id": run_id,
                    },
                },
            )
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
