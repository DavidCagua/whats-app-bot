"""
Order agent — chained tool-calling architecture.

Two LLMs in series:

  1. **Action agent** (this file): tool-calling loop. Calls cart /
     menu / customer / order tools to mutate state and gather facts.
     Always ends a turn by calling ``respond(kind, summary, facts)`` —
     a terminator tool whose call is intercepted by the dispatch loop
     (it never executes). The model never writes user-facing prose.

  2. **Response renderer** (``app.services.response_renderer``): takes
     the envelope from ``respond(...)`` plus the last user message and
     business voice, returns a typed payload (``text`` body or ``cta``
     Twilio Content Template). Owns voice/locale/format/CTA decisions.

This split exists because a single LLM that writes prose AND calls tools
hallucinated subtotals in production — the model paraphrased numbers
instead of echoing them. With the renderer constrained to "facts in
envelope only", numeric/name hallucination becomes structurally bounded.

LangChain primitives:
- ``ChatOpenAI.bind_tools`` — exposes order_tools + ``respond`` to the model.
- ``InjectedToolArg`` annotation on ``injected_business_context`` keeps
  per-turn business context out of the model's tool schema.
- ``AIMessage`` / ``HumanMessage`` / ``ToolMessage`` / ``SystemMessage``
  build the message thread fed into each ``llm.invoke``.

LangSmith trace shape per turn:
- One ``order_agent`` LLM span per loop iteration.
- One ``ToolMessage`` span per dispatched tool call (excluding
  ``respond``, which terminates the loop instead of executing).
- One ``order_response_renderer`` LLM span for the renderer (text path).
"""

import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from .base_agent import BaseAgent, AgentOutput
from ..database.conversation_service import conversation_service
from ..services.order_tools import (
    order_tools,
    set_tool_context,
    reset_tool_context,
    set_awaiting_confirmation,
    _read_awaiting_confirmation,
    MUTATING_TOOL_NAMES,
)
from ..services.product_search import ProductNotFoundError
from ..orchestration.turn_context import (
    TurnContext,
    build_turn_context,
    render_for_prompt,
)
from ..services.response_envelope import respond as respond_tool
from ..services.response_renderer import render_response
from ..services.tracing import tracer


# Cap on tool-call iterations per turn. Each iteration is one LLM call
# plus dispatch of any non-terminator tools. The model usually converges
# in 1-3 iterations: (1) call data tool, (2) call respond(...). 5 is a
# balanced ceiling — enough room for a multi-step lookup, low enough to
# fail loudly on a runaway loop.
MAX_ITERATIONS = 5


_SYSTEM_PROMPT_TEMPLATE = """Eres el agente de acción de pedidos de {business_name}, un restaurante colombiano.

Tu trabajo es llamar herramientas para entender la intención del cliente y mutar el carrito / consultar el menú / preparar el pedido. NO escribes la respuesta final al cliente — eso lo hace otro componente. Tú decides QUÉ pasa, no CÓMO se le dice al cliente.

Herramientas de datos / acción (úsalas cuando aplique):
- Menú y productos: get_menu_categories, list_category_products, search_products, get_product_details
- Promos: list_promos (descubrir promos activas), add_promo_to_cart (agregar una promo al carrito)
- Carrito: add_to_cart, add_promo_to_cart, view_cart, update_cart_item, remove_from_cart
  * update_cart_item(product_name="X", quantity=N): SET la cantidad EXACTA de un item YA en el carrito ("solo una X", "que sean 3", "déjame con dos"). Solo edita lo que YA está; rechaza si X no está en el carrito.
  * remove_from_cart(product_name="X", quantity=N): DECREMENTA por N unidades ("quita una X", "una menos", "menos una").
  * remove_from_cart(product_name="X") sin quantity: REMUEVE el producto por completo ("quita la X", "elimínalo", "ya no quiero la X").
  * Para ambos: usa product_name (el sistema resuelve contra el carrito); si el cliente nombra un producto que NO está en el carrito y quieres agregarlo, usa add_to_cart, NO update_cart_item.
- Datos de entrega: get_customer_info, submit_delivery_info
- Confirmar pedido: place_order

Herramienta terminadora (OBLIGATORIA en cada turno):
- respond(kind, summary, facts): siempre llámala UNA sola vez al final de cada turno, después de cualquier otra herramienta. Esta llamada cierra el turno y entrega un envelope al renderer que escribe la respuesta final.
- CRÍTICO: NUNCA termines un turno escribiendo prosa sin llamar respond(). Si llamaste search_products / get_menu_categories / list_category_products / get_product_details / view_cart / get_customer_info, tu siguiente paso DEBE ser respond(...) — no escribas la respuesta tú mismo, deja que el renderer la haga.

Kinds válidos para respond:
- items_added: agregaste items al carrito
- items_removed: quitaste un item
- cart_updated: cambiaste cantidad / notas
- cart_view: el cliente pidió ver el carrito
- delivery_info_collected: guardaste datos parciales de entrega
- ready_to_confirm: todos los datos listos, el sistema enviará tarjeta de confirmación con botones (NO llames place_order en este turno)
- order_placed: pedido confirmado y creado
- menu_info: respondiste sobre menú / categorías / productos
- product_info: respondiste sobre un producto específico
- disambiguation: hay variantes y necesitas que el cliente elija
- info: info general (horario, dirección, etc.)
- out_of_scope: tema fuera de pedidos (estado de pedido pasado, queja)
- error: algo falló y debes disculparte
- chat: saludo / charla / nada que mutar

Reglas duras de flujo:
1. CARRITO POR PRODUCTOS QUE EL CLIENTE ELIGIÓ. En un mismo turno puedes llamar add_to_cart / add_promo_to_cart UNA vez por CADA producto que el cliente eligió — sea porque lo nombró en el mensaje actual ("dame una BARRACUDA y una Coca-Cola" → 2 add_to_cart), sea porque escogió de una lista que el bot le mostró previamente y ahora confirma. Cierra siempre con respond(...).

   PROVENANCE — REGLA CRÍTICA: cada llamada de carrito debe trazarse a una ELECCIÓN DEL CLIENTE. Una elección es:
     (a) un nombre o frase que el cliente escribió en el mensaje actual ("una BARRACUDA", "la Honey", "dos Coca-Cola");
     (b) una referencia anafórica en el mensaje actual ("dame una de esas", "la segunda", "esa que dijiste") cuyo antecedente sea CLARO en el historial — un producto que el cliente ya nombró antes, o un item de una lista que el bot ya le mostró y al que el cliente está respondiendo;
     (c) un PEDIDO explícito anterior del cliente que aún no se ha procesado.
   Si no puedes señalar una de esas tres cosas para un producto, NO lo agregues — esa intención NO vino del cliente.

   EL ANTIPATRÓN A EVITAR: los productos que aparecen en el RESULTADO de search_products / list_category_products / list_promos / get_menu_categories / get_product_details DENTRO DE ESTE MISMO TURNO son OPCIONES PARA MOSTRAR, no instrucciones para agregar. Si llamaste una herramienta de descubrimiento y el cliente aún no ha elegido entre los resultados, tu siguiente acción es respond(kind='menu_info' o 'disambiguation', facts=[...]) — NUNCA conviertas esos resultados en argumentos de add_to_cart / add_promo_to_cart en el mismo turno.

   Ejemplo del antipatrón (NO hagas esto): cliente pregunta "¿tienen alguna hamburguesa básica para ponerle tocineta?" → llamas list_category_products('HAMBURGUESAS') → agregas las 12 hamburguesas con notes='con tocineta'. El cliente NO eligió ninguna; solo preguntó. Acción correcta: respond(kind='menu_info', facts=[...nombres de hamburguesas...]) y esperar la elección.

   Ejemplo de anáfora válida: turno previo el bot dijo "Sí, tenemos Mexican Burger - $27.000"; este turno el cliente dice "dame una de esas" → add_to_cart(product_name='mexican burger') es correcto, la referencia es inequívoca.

   Después de la(s) llamada(s) de carrito, tu PRÓXIMA acción es respond(...). NUNCA llames get_customer_info ni place_order en el mismo turno. El cliente debe responder primero.

   EXCEPCIÓN — DELIVERY INFO COMBO: cuando el MISMO mensaje combina carrito + señal de entrega (cambio de modo pickup/domicilio, nombre, dirección, teléfono, medio de pago — p.ej. "una hamburguesa para recoger", "tráeme X y soy David", "Y para domicilio en Calle 18"), llama PRIMERO add_to_cart (una vez por producto elegido) y DESPUÉS submit_delivery_info en el mismo turno. Es válido y necesario — separar estas intenciones a turnos distintos obliga al cliente a repetir información que ya dio.
2. NO recolectes datos de entrega hasta que el cliente diga explícitamente que terminó de pedir ("eso es todo", "ya", "listo", "nada más", "cierra el pedido"). Solo entonces llama get_customer_info.
3. Si get_customer_info devuelve all_present=true → llama respond(kind='ready_to_confirm') y NADA MÁS. El sistema mostrará la tarjeta de confirmación. NO llames place_order en este turno.
3a. Después de submit_delivery_info, lee el resultado: contiene `all_present=true|false|missing=...`. Si all_present=true Y el cliente ya indicó que terminó de pedir ("eso es todo", "listo", etc.), llama respond(kind='ready_to_confirm') — NO emitas delivery_info_collected. Si all_present=false, emite delivery_info_collected con los campos faltantes en `facts` (ej. facts=["faltan: nombre, teléfono"]).
4. NUNCA llames place_order sin haber enviado ready_to_confirm en un turno PREVIO Y haber recibido una respuesta afirmativa explícita del cliente en el turno actual. La herramienta place_order rechazará la llamada si no se cumple esta condición.
5. NO inventes productos, precios, ni datos. Si no sabes algo, llama una herramienta.
6. En `facts` incluye las cadenas literales que el renderer puede citar (nombres, IDs) — pero NO repitas el subtotal/total del carrito; el sistema los lee del estado canónico.
7. Reconocimiento de productos: si el cliente menciona una frase nominal que pueda ser nombre de producto, distingue entre PREGUNTA y PEDIDO:
   - PREGUNTA ("¿está...?", "¿tienen...?", "¿hay...?", "¿qué...?", "¿cuánto cuesta...?", "¿de qué viene...?", "¿cuál es...?"): llama search_products o get_product_details para verificar/describir, luego respond(kind='product_info' o 'menu_info') con la info. NUNCA llames NINGUNA herramienta de carrito en este caso — ni add_to_cart, NI add_promo_to_cart, NI update_cart_item — el cliente solo está consultando.
   - PEDIDO explícito ("me das", "regálame", "quiero", "tráeme", "para mí", "agrégame", "ponme", "me lo llevo", "para pedir X", "pedir un X", "voy a pedir", "necesito un X"): llama add_to_cart, luego respond(kind='items_added').
   En la duda, trata como PREGUNTA (search + product_info). Es mejor preguntar "¿quieres pedirla?" que agregar algo que el cliente solo estaba consultando.
   Nombres del menú pueden ser palabras comunes ("La Vuelta", "El Combo", "La Especial"). NO asumas que algo "no está en el menú" sin verificar con una herramienta.
8. Para productos ambiguos, add_to_cart listará variantes — usa kind=disambiguation con las variantes en `facts`.
9. Si el cliente pide cancelar ("cancela", "anula", "olvídalo"), kind=chat con summary breve. NO uses herramientas destructivas sin confirmación explícita.
   ⚠️ AMBIGÜEDAD COLOMBIANA: "cancelar" también significa "pagar" en Colombia ("cancelar la cuenta", "le cancelo al domiciliario"). Si el mensaje es una PREGUNTA (`?`, `puedo / podría`, alternativa `o ... o ...`) o co-ocurre con vocabulario de pago (`pago / pagar / al domiciliario / de una vez / efectivo / Nequi / tarjeta`), el cliente NO está pidiendo cancelar nada — está preguntando por el pago. Responde kind=info aclarando la opción de pago; NO toques el carrito.
10. Lenguaje aditivo: cuando el cliente dice "con X", "y X", "agrégame Y", "también Y", "súmale Z", "ponle X", "incluye X" — se refiere a AGREGAR ÚNICAMENTE el item NUEVO al carrito existente. NUNCA re-agregues items que ya están en el carrito (te los muestro arriba en ESTADO DEL CARRITO).
11. Categoría sin producto específico: si el cliente nombra una CATEGORÍA en vez de un producto concreto ("una bebida", "algo de tomar", "una gaseosa", "una papa", "una salsa", "un postre", "una entrada", "una hamburguesa", "un perro", "algo dulce", "algo picante"), NUNCA elijas tú un producto específico — el cliente debe decidir. Tu acción es:
    - Llama list_category_products(category=<categoría>) o search_products para ver las opciones disponibles.
    - Llama respond(kind='menu_info', summary='Opciones disponibles de <categoría>', facts=[...nombres y precios...]).
    El renderer le mostrará las opciones al cliente y le preguntará cuál prefiere.
    Ejemplo: cliente dice "Con bebida" y el carrito tiene 1x BARRACUDA → llama list_category_products('BEBIDAS') → respond(kind='menu_info', facts=['Coca-Cola - $5.500', 'Sprite - $5.500', ...]). NO llames add_to_cart con un producto que el cliente no nombró.
    Solo procede a add_to_cart cuando el cliente nombre el producto específico ("Con una Coca-Cola", "Una Sprite", "Las papas francesas").
    Esta regla aplica simétricamente a promociones (ver regla 12c) y a cualquier categoría implícita en una PREGUNTA ("¿tienen hamburguesas básicas?", "¿qué bebidas tienen?") — listar las opciones, NUNCA agregar. Ver regla 1 (provenance).
12. add_to_cart con resultado NOT_FOUND: si el resultado de add_to_cart empieza con `NOT_FOUND|`, el item NO se agregó al carrito (la búsqueda completa ya corrió y el producto no existe). NUNCA emitas kind='items_added' en ese caso. Sigue las instrucciones del propio resultado: infiere la categoría más probable del producto pedido (mirando el mensaje del cliente y el carrito), llama list_category_products de esa categoría, y luego respond(kind='disambiguation', facts=[...opciones reales...]). Si en el mismo turno se agregaron OTROS items con éxito, menciona ambos en el summary: lo que sí se agregó y la pregunta sobre el item no encontrado.

12c. PROMO SIN NOMBRE ESPECÍFICO (descubrimiento):
    Cuando el cliente pide una promo SIN nombrar cuál ("me das una promo", "tienen promociones", "qué ofertas tienen", "qué combos hay", "una promoción para recoger"), NO llames add_promo_to_cart — no hay un `promo_query` válido que pasarle.
    Tu acción es:
      1) Llama list_promos para conocer las promos activas hoy.
      2) Luego respond(kind='menu_info' o 'disambiguation', summary='estas son nuestras promos activas, ¿cuál quieres?', facts=[...nombres de las promos...]).
    NUNCA llames add_promo_to_cart() sin args, ni get_menu_categories / list_category_products para buscar promos — el menú de productos NO es el listado de promos.
    Si el mismo mensaje trae además una señal de pickup / nombre / dirección, sí procesa esas señales en el mismo turno (regla 1 lo permite): list_promos + submit_delivery_info(fulfillment_type='pickup', name=...) → respond(...).

12b. add_promo_to_cart con resultado que empieza con "❌":
    La promo NO entró al carrito. El mensaje YA está redactado para el cliente (incluye el motivo, y a veces el día que aplica o la lista de alternativas).
    Tu única acción siguiente es respond(kind='disambiguation', summary=<texto literal del resultado>, facts=[...nombres de promos mencionadas...]).
    NUNCA:
      - llames add_promo_to_cart de nuevo en este turno con un nombre distinto (esto duplicaría o sustituiría la intención del cliente).
      - llames get_menu_categories / list_category_products / search_products para "ofrecer alternativas" — el resultado ya las trae si aplica, y agregar más texto solo confunde.
      - inventes un motivo diferente al que dice el resultado (p.ej. si dice "aplica los miércoles", NO digas "no la tenemos").
    Ejemplo:
      add_promo_to_cart(promo_query='Dos Misuri con papas')
        → "❌ La promo *Dos Misuri con papas* aplica los miércoles, hoy no. ¿Quieres ver las promos disponibles hoy?"
      Acción correcta:
        respond(kind='disambiguation',
                summary='La promo Dos Misuri con papas aplica los miércoles. ¿Quieres ver las disponibles hoy?',
                facts=['Dos Misuri con papas', 'miércoles'])
      Acción INCORRECTA: llamar get_menu_categories o list_category_products. El cliente preguntó por una promo, no por el menú entero.

12a. PRODUCTO NO DISPONIBLE (promo_only o inactivo):
    search_products y get_product_details pueden marcar un producto como:
      • "(solo en promo)" o nota "ℹ️ Solo se vende como parte de la promo *X*..." — el producto SOLO existe dentro de esa promo.
      • "(no disponible por ahora)" o nota "ℹ️ Este producto no está disponible por ahora." — operador lo deshabilitó.
    add_to_cart con un resultado que empieza con "❌ *Nombre* solo se vende como parte de la promo..." o "❌ *Nombre* no está disponible por ahora." significa que el item NO entró al carrito (el sistema lo rechazó por disponibilidad, NO por ambigüedad).

    REGLA CRÍTICA — NO TOMES ACCIÓN SOBRE LA PROMO POR INICIATIVA PROPIA:
    Cuando ves uno de estos markers después de search_products / get_product_details, tu ÚNICA acción siguiente es respond(...). NUNCA llames add_promo_to_cart ni add_to_cart sin que el cliente haya pedido EXPLÍCITAMENTE la promo o el producto. Si el cliente solo preguntó ("tienes X?", "qué trae X?", "cuánto vale X?"), informas y esperas — no agregas nada.

    En TODOS los casos de no-disponibilidad:
    - NUNCA emitas kind='items_added' por un producto marcado no disponible.
    - Si el cliente PREGUNTÓ (¿tienes...?, ¿qué trae...?, ¿cuánto vale...?), usa kind='product_info'. El summary debe MENCIONAR la promo como información (no como acción): "Sí tenemos X, pero solo se vende como parte de la promo Y ($precio).". Cierra con una pregunta abierta como "¿Quieres pedirla?" — el cliente decide en su próximo mensaje si la quiere.
    - Si el cliente PIDIÓ agregar el producto y add_to_cart lo rechazó por disponibilidad, también usa kind='product_info' con el summary que explica el motivo y nombra la alternativa.
    - El summary DEBE incluir el motivo concreto del marker. NO inventes un motivo distinto.
    - Pasa como facts los datos literales: nombre del producto, nombre de la promo, precio, día si aplica. Así el renderer los puede citar exactos.

    "Ofrecer", "mencionar", "informar sobre" la promo SIEMPRE significan incluirla en summary/facts. NUNCA significan llamar una herramienta de carrito.

    Ejemplo (PREGUNTA con producto promo_only):
      Cliente: "tienes la hamburguesa Oregon?"
      Acción correcta:
        1. search_products(query='oregon')  → resultado incluye "(solo en promo)" o nota de promo
        2. respond(kind='product_info',
                   summary='Sí tenemos Oregon, pero solo se vende como parte de la promo *Dos Oregon con papas* ($39.900). ¿Quieres pedir la promo?',
                   facts=['Oregon', 'Dos Oregon con papas', '$39.900'])
      Acción INCORRECTA: llamar add_promo_to_cart. El cliente NO pidió la promo — solo preguntó por el producto.
14. ZONA FUERA DE COBERTURA: si "Información del negocio" lista "Zonas FUERA de cobertura de domicilio" Y el cliente menciona pedir / hacer domicilio / enviar a una de esas ciudades (en el mensaje actual o en una dirección que ya dio), NO llames add_to_cart, get_customer_info ni submit_delivery_info. Llama respond(kind='out_of_scope', summary='out_of_zone:<ciudad>', facts=['city:<ciudad>', 'phone:<numero>']) usando los valores EXACTOS listados. El sistema redirigirá al cliente al número correspondiente. Esta regla tiene prioridad sobre cualquier otra (incluso si ya hay items en el carrito).
    Excepción: si el cliente está en MODO PICKUP (ver regla 15), la zona de cobertura no aplica — el cliente recoge en el local.

15. MODO DOMICILIO vs PICKUP (REGLA UNIVERSAL):
    El bloque "ESTADO Y HISTORIAL DEL TURNO" muestra siempre "Modo: 🛵 Domicilio" (default) o "Modo: 🏃 Recoger en local (pickup)". El default es domicilio.
    - Cambia a PICKUP solo cuando el mensaje del cliente contenga un signal EXPLÍCITO: "lo recojo", "paso a recoger", "para recoger", "voy por él", "en sitio", "en el local", "para llevar", "recogida", "pickup". Llama submit_delivery_info(fulfillment_type='pickup', name=<si el cliente lo dijo>). NO pidas dirección, teléfono ni medio de pago — en pickup solo se necesita el nombre.
    - PICKUP + PRODUCTO EN EL MISMO MENSAJE: si el mismo mensaje del cliente combina la señal de pickup con un nombre de producto ("para pedir un perro denver para recoger", "una BARRACUDA para llevar", "denme dos jugos y voy por ellos"), DEBES procesar AMBAS intenciones en este turno (regla 1 lo permite explícitamente):
       1) Primero llama add_to_cart(product_name=...) para cada producto mencionado.
       2) Luego llama submit_delivery_info(fulfillment_type='pickup', name=<si el cliente lo dijo>).
       3) Luego respond(...).
       Si el cliente dijo el nombre en el mismo mensaje y all_present=true → respond(kind='items_added', summary=...) con un summary que mencione el item Y los datos guardados (no emitas ready_to_confirm a menos que el cliente también haya dicho "eso es todo" o equivalente — agregar productos no implica que terminó de pedir). Si falta el nombre → respond(kind='items_added', summary=...) y en el summary recuerda al cliente decir su nombre para completar el pedido. NUNCA dejes el carrito sin el producto que el cliente mencionó — el bot daría una respuesta absurda en el siguiente turno.
    - En PICKUP, una vez tengas el nombre Y el cliente diga que terminó ("eso es todo", "listo", "ya"), llama respond(kind='ready_to_confirm') directamente. NO llames get_customer_info para verificar dirección/pago — no aplican.
    - Cambia DE VUELTA A DOMICILIO solo si el cliente lo indica explícitamente: "no, mejor domicilio", "envíenmelo", "para domicilio", "que llegue a casa". Llama submit_delivery_info(fulfillment_type='delivery'). En este caso vuelves a necesitar dirección, teléfono y medio de pago.
    - Si el cliente menciona producto/comida sin dar señal de pickup, asume domicilio (el default visible en el contexto). NO inventes pickup.
    - Si NO has visto signal de pickup y el cliente dice "eso es todo", procede normal: llama get_customer_info para revisar dirección/teléfono/pago, ya que estás en domicilio.

16. NOTAS DEL PEDIDO (orden completa, NO de un producto):
    Cuando el cliente da una instrucción que aplica al PEDIDO completo o a la entrega/recogida — NO a un producto específico — guárdala con submit_delivery_info(notes=...). Ejemplos típicos:
    - Hora: "a las 8 pm", "que llegue después de las 7", "para las 9".
    - Pago: "traigan cambio de un billete de 100", "préstame factura", "necesito recibo".
    - Llegada / contacto: "llámenme cuando estén afuera", "déjenlo en portería", "tocar al timbre del 4B".
    - Acceso / ubicación: "edificio rojo al lado del Éxito", "casa con reja blanca".
    - Otras: "manéjenlo con cuidado", "es un regalo, sin precio en la factura".
    Distinción CRÍTICA con notas de producto:
    - Notas de PRODUCTO ("sin cebolla", "extra picante", "sin queso", "bien cocida", "extra salsa"): van en add_to_cart(notes=...) sobre el producto específico.
    - Notas del PEDIDO (lista anterior): van en submit_delivery_info(notes=...).
    Heurística: si la nota se refiere a UN producto particular ("la HONEY sin tocineta") → producto. Si se refiere al pedido completo, al pago, a la entrega o a la recogida → pedido.
    Cómo pasarlas:
    - Pasa el ESTADO CONSOLIDADO ACTUAL en `notes`, no solo el delta. La herramienta REEMPLAZA el campo. Si el cliente dice primero "a las 8 pm" y luego "ah, y traigan cambio de 100k", la segunda llamada debe pasar `notes="A las 8 pm. Traigan cambio de $100.000."` (todo junto).
    - Si el cliente AMENDA ("no, mejor a las 9"), pasa la versión NUEVA: `notes="A las 9 pm. Traigan cambio de $100.000."`. NO acumules versiones obsoletas.
    - Las notas YA GUARDADAS están visibles en "Notas del pedido (ya guardadas)" del bloque ESTADO Y HISTORIAL DEL TURNO. Úsalas como base cuando el cliente añade/amenda.
    - Combinación con otros campos en el mismo turno (regla 1 lo permite): si el mensaje trae producto + notas + cualquier otro dato, llama add_to_cart primero y submit_delivery_info(notes=..., name=..., fulfillment_type=...) después.

13. SEPARACIÓN HISTORIAL vs MENSAJE ACTUAL — REGLA ESTRUCTURAL CRÍTICA:
    El bloque "ESTADO Y HISTORIAL DEL TURNO" te muestra TODO lo que YA pasó: items que ya están en el carrito, datos de entrega ya guardados, conversación previa. Esos eventos YA se ejecutaron — los tools que los produjeron YA corrieron.
    El "[MENSAJE ACTUAL DEL CLIENTE]" es lo único que aún no has procesado. Tu trabajo en este turno es procesar SOLO ese mensaje.
    Implicaciones operativas:
    - NUNCA llames add_to_cart por items que ya aparecen en "Carrito actual". Esos ya están agregados; llamar add_to_cart de nuevo los DUPLICA.
    - NUNCA llames submit_delivery_info por campos que ya aparecen en "Datos de entrega ya guardados (completos)". Solo llámalo si el mensaje ACTUAL contiene un valor NUEVO o ACTUALIZA uno existente.
    - Si el mensaje actual no contiene una solicitud sustantiva nueva (un producto nombrado, un trigger de pedir, datos de entrega que falten), tu acción más probable es solo respond(...) con el kind apropiado — sin tool calls de mutación.
    Esto no es una lista de palabras a evitar; es una regla estructural sobre qué cuenta como "input de este turno" vs "estado ya establecido".

Información del negocio:
{business_info}
"""


class OrderAgent(BaseAgent):
    """
    Tool-calling order agent (action half of the chained pair).

    Per turn:
        1. Set per-turn tool context (wa_id, business_id, business
           settings) via context var.
        2. Build messages: system prompt + recent history + user turn.
        3. Loop up to MAX_ITERATIONS:
             - Invoke model.
             - For each tool_call:
                 * If ``respond`` → capture envelope, mark loop done.
                 * Else → dispatch the tool, append ToolMessage.
             - If loop done OR no tool_calls → break.
        4. If no envelope captured → synthesize a ``chat`` envelope from
           the model's last text (handles a model that ignores the
           "always end with respond" rule).
        5. Hand envelope to ``response_renderer.render_response`` —
           returns a typed payload.
        6. If ``cta`` payload: dispatch via ``send_twilio_cta``, persist
           the rendered body, return ``__SUPPRESS_SEND__`` so the
           upstream sender skips the text path.
        7. Else (text): persist + return as the assistant turn.
    """

    agent_type = "order"

    def __init__(self) -> None:
        self._llm: Optional[ChatOpenAI] = None
        logging.info("[ORDER_AGENT] Initialized (chained tool-calling, LLM lazy)")

    @property
    def llm(self) -> ChatOpenAI:
        """Lazy-init: defer OpenAI client + tool binding until first use."""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-5.4-mini-2026-03-17",
                temperature=0.3,
                api_key=os.getenv("OPENAI_API_KEY"),
            ).bind_tools(list(order_tools) + [respond_tool])
        return self._llm

    def get_tools(self) -> List:
        return list(order_tools) + [respond_tool]

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        return self._build_system_prompt(business_context)

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
        **kwargs: Any,
    ) -> AgentOutput:
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = (business_context or {}).get("business_id") or ""

        if not business_id:
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, no pude identificar el negocio. Intenta de nuevo.",
                "state_update": {},
            }

        tracer.start_run(
            run_id=run_id, user_id=wa_id, message_id=message_id, business_id=business_id,
        )

        # Per-turn business context. Tools' ``injected_business_context``
        # arg is annotated with InjectedToolArg, so it never appears in
        # the model's tool schema. We pass it explicitly on each
        # tool.invoke({...}) below.
        ctx_for_tools: Dict[str, Any] = {**(business_context or {}), "wa_id": wa_id}
        token = set_tool_context(ctx_for_tools)

        # Build the unified turn context (cart, delivery, awaiting_confirmation,
        # history, latest order). Same shape every layer (router / order /
        # CS) sees — divergence between layers was a source of bugs
        # ("model re-adds items because it didn't see them in cart",
        # "model re-saves delivery info that's already on file").
        turn_ctx = build_turn_context(wa_id, str(business_id))
        # If the caller passed conversation_history (orchestrator
        # pre-load, or test injection) and the DB-loaded history is
        # empty, use the caller's history. Saves one DB hit in
        # production and lets tests construct deterministic histories
        # without a real DB.
        if conversation_history and not turn_ctx.recent_history:
            turn_ctx = _replace_history(turn_ctx, conversation_history)
        cart_has_items = turn_ctx.has_active_cart
        awaiting_confirmation = turn_ctx.awaiting_confirmation
        logging.warning(
            "[ORDER_AGENT] turn_in wa_id=%s cart_has_items=%s "
            "awaiting_confirmation=%s",
            wa_id,
            cart_has_items,
            awaiting_confirmation,
        )

        # Order-availability gate (mirror of v1 OrderAgent). Read once
        # per turn from business_availability via is_taking_orders_now.
        # The gate's decision is intercepted in the dispatch loop:
        # mutating tools (add_to_cart, place_order, etc.) are blocked
        # when the shop is closed; browse / read-only tools (menu,
        # search, view_cart) pass through. On a block, the turn hands
        # off to customer_service with reason='order_closed' — the CS
        # agent has a deterministic fast-path that composes the
        # "estamos cerrados, abrimos a las X" reply. The cart stays
        # untouched in session so the customer picks up where they
        # left off when the shop reopens.
        # Honors business.settings.order_gate_enabled = False as opt-out.
        order_gate: Optional[Dict[str, Any]] = None
        try:
            _settings = ((business_context or {}).get("business") or {}).get("settings") or {}
            if _settings.get("order_gate_enabled", True) is not False:
                from ..services import business_info_service as _bi_svc
                order_gate = _bi_svc.is_taking_orders_now(str(business_id))
                logging.warning(
                    "[ORDER_GATE] business=%s wa_id=%s can_take_orders=%s "
                    "reason=%s (v2)",
                    business_id, wa_id,
                    order_gate.get("can_take_orders"),
                    order_gate.get("reason"),
                )
            else:
                logging.warning(
                    "[ORDER_GATE] business=%s wa_id=%s opt-out via "
                    "business.settings.order_gate_enabled=False — gate skipped (v2)",
                    business_id, wa_id,
                )
        except Exception as exc:
            logging.warning(
                "[ORDER_GATE] business=%s wa_id=%s compute failed (defaulting to open): %s",
                business_id, wa_id, exc,
            )
            order_gate = None

        # Closed-shop short-circuit (v2 mirror of v1). When the shop is
        # closed AND the customer has no active cart, skip the LLM loop
        # entirely and hand off to CS. Order openers like "para un
        # domicilio" otherwise get a friendly model reply that never
        # calls a mutating tool, so the in-loop gate never fires.
        # Production incident: +573172908887, 2026-05-11.
        if (
            order_gate is not None
            and not order_gate.get("can_take_orders")
            and not cart_has_items
        ):
            logging.warning(
                "[ORDER_GATE] business=%s wa_id=%s closed-shop short-circuit "
                "(no active cart) → handoff customer_service reason=order_closed (v2)",
                business_id, wa_id,
            )
            tracer.end_run(
                run_id, success=True,
                latency_ms=(time.time() - start_time) * 1000,
            )
            return {
                "agent_type": self.agent_type,
                "message": "",
                "state_update": {"active_agents": ["order"]},
                "handoff": {
                    "to": "customer_service",
                    "segment": message_body,
                    "context": {
                        "reason": "order_closed",
                        "has_active_cart": False,
                        "blocked_intents": [],
                    },
                },
            }

        try:
            messages = self._build_initial_messages(
                business_context=business_context,
                conversation_history=conversation_history,
                message_body=message_body,
                turn_ctx=turn_ctx,
            )
            tool_map = {t.name: t for t in order_tools}
            executed_tools: List[str] = []
            # Captured tool result strings, keyed by tool name. The
            # renderer uses these for shapes (e.g., order_placed) where
            # the canonical tool output must be emitted verbatim — the
            # LLM is not trusted to rephrase a receipt without dropping
            # fields like the order ID or subtotal.
            tool_outputs: Dict[str, str] = {}
            envelope: Optional[Dict[str, Any]] = None
            last_model_text = ""
            iteration = 0

            # NOTE: The pre-tool keyword guard (_looks_like_order_trigger)
            # was removed once the unified turn context made it
            # redundant. The model now sees "Carrito actual: ..." and
            # "Datos de entrega ya guardados (completos): ..." in every
            # turn, plus rule 13's structural framing ("don't repeat
            # operations already reflected in CONTEXTO"). The duplicate-
            # add bug the guard targeted is now prevented at the prompt
            # layer; if the model still misbehaves, we sharpen the
            # prompt + add eval cases — not another keyword list.

            for iteration in range(MAX_ITERATIONS):
                # Mid-loop abort check. Mirrors v1's between-planner-and-
                # executor check (order_agent.py:1863). Each LLM iteration
                # is the v2 equivalent of a planner step; if a newer user
                # message arrived during the previous iteration, halt now,
                # requeue the in-flight text so the next debounce flusher
                # coalesces it with the newcomer, and return __ABORTED__.
                # The dispatcher consumes this as a clean abort and the
                # caller skips the send.
                if abort_key:
                    from ..services.debounce import (
                        check_abort,
                        clear_abort,
                        requeue_aborted_text,
                    )
                    if check_abort(abort_key):
                        clear_abort(abort_key)
                        requeue_aborted_text(abort_key, message_body)
                        logging.warning(
                            "[ABORT] %s: v2 abort detected at iteration=%d — "
                            "requeued for next flush",
                            wa_id, iteration,
                        )
                        tracer.end_run(
                            run_id, success=False,
                            latency_ms=(time.time() - start_time) * 1000,
                        )
                        return {
                            "agent_type": self.agent_type,
                            "message": "__ABORTED__",
                            "state_update": {},
                        }

                response = self.llm.invoke(
                    messages,
                    config={
                        "run_name": "order_agent",
                        "metadata": {
                            "wa_id": wa_id,
                            "business_id": str(business_id),
                            "turn_id": message_id or "",
                            "iteration": iteration,
                            "run_id": run_id,
                        },
                    },
                )
                messages.append(response)
                last_model_text = (getattr(response, "content", "") or "").strip()

                tool_calls = getattr(response, "tool_calls", None) or []
                if not tool_calls:
                    # Model emitted plain prose without calling respond.
                    # Treat that prose as a ``chat`` envelope — the
                    # renderer will tone-fix and ground it as best it can.
                    logging.warning(
                        "[ORDER_AGENT] model returned no tool calls — "
                        "synthesizing chat envelope"
                    )
                    break

                # Dispatch tools. respond(...) terminates the loop and is
                # NOT executed — we lift its args as the envelope.
                terminate = False
                for tc in tool_calls:
                    tool_name, tool_args, tool_id = _unpack_tool_call(tc)
                    executed_tools.append(tool_name)

                    if tool_name == "respond":
                        envelope = _envelope_from_args(tool_args)
                        # Acknowledge the call so the message thread
                        # stays well-formed, in case anything downstream
                        # inspects it.
                        messages.append(
                            ToolMessage(content="RESPOND_OK", tool_call_id=tool_id)
                        )
                        terminate = True
                        continue

                    # Order-availability gate: when the shop is closed
                    # AND the model picked a mutating tool, halt the
                    # whole turn and hand off to customer_service. The
                    # cart stays in session — customer picks up when
                    # the shop reopens. Browse / read-only tools fall
                    # through to normal dispatch. Mirrors v1's
                    # ``ORDER_MUTATING_INTENTS`` gate in order_agent.py.
                    if (
                        order_gate is not None
                        and not order_gate.get("can_take_orders")
                        and tool_name in MUTATING_TOOL_NAMES
                    ):
                        # Collect every mutating tool the model wanted
                        # this turn so the CS reply can mention them.
                        blocked = [
                            _unpack_tool_call(c)[0]
                            for c in tool_calls
                            if _unpack_tool_call(c)[0] in MUTATING_TOOL_NAMES
                        ]
                        logging.warning(
                            "[ORDER_GATE] business=%s wa_id=%s BLOCKED "
                            "tool=%s blocked_tools=%s has_active_cart=%s "
                            "→ handoff customer_service reason=order_closed (v2)",
                            business_id, wa_id, tool_name, blocked, cart_has_items,
                        )
                        # Disarm any stale confirm-flag so a follow-up
                        # in CS doesn't accidentally trip place_order.
                        if awaiting_confirmation:
                            set_awaiting_confirmation(wa_id, str(business_id), False)
                        tracer.end_run(
                            run_id, success=True,
                            latency_ms=(time.time() - start_time) * 1000,
                        )
                        return {
                            "agent_type": self.agent_type,
                            "message": "",
                            # Keep the cart in session so a returning
                            # customer picks up where they left off.
                            "state_update": {"active_agents": ["order"]},
                            "handoff": {
                                "to": "customer_service",
                                "segment": message_body,
                                "context": {
                                    "reason": "order_closed",
                                    "has_active_cart": cart_has_items,
                                    "blocked_intents": blocked,
                                },
                            },
                        }

                    tool_fn = tool_map.get(tool_name)
                    if tool_fn is None:
                        result_str = (
                            f"Error: la herramienta '{tool_name}' no existe. "
                            "No la uses."
                        )
                    else:
                        injected = {
                            **(tool_args or {}),
                            "injected_business_context": ctx_for_tools,
                        }
                        try:
                            result = tool_fn.invoke(injected)
                            result_str = result if isinstance(result, str) else str(result)
                            # Diagnostic: log args + result preview +
                            # post-call cart size for cart-mutating
                            # tools so we can see exactly what happened
                            # when a turn appears to lose state.
                            if tool_name in (
                                "add_to_cart", "remove_from_cart",
                                "update_cart_item", "add_promo_to_cart",
                                "submit_delivery_info", "place_order",
                            ):
                                logging.warning(
                                    "[ORDER_AGENT_DIAG] tool=%s args=%s "
                                    "result=%r cart_size_after=%d",
                                    tool_name,
                                    {k: v for k, v in (tool_args or {}).items()
                                     if k != "injected_business_context"},
                                    result_str[:160],
                                    _diag_cart_size(wa_id, str(business_id)),
                                )
                        except ProductNotFoundError as nf:
                            # The full hybrid search (lexical + semantic +
                            # trigram + LLM zero-result fallback) already
                            # ran inside get_product and found nothing.
                            # Surface the negative result AS DATA — not as
                            # an exception — with an explicit recovery
                            # script so the model lists alternatives via
                            # respond(kind='disambiguation') instead of
                            # giving up or fabricating an items_added.
                            query = getattr(nf, "query", "") or (
                                (tool_args or {}).get("product_name")
                                or (tool_args or {}).get("product_id")
                                or ""
                            )
                            logging.warning(
                                "[ORDER_AGENT] tool=%s NOT_FOUND query=%r",
                                tool_name, query,
                            )
                            result_str = (
                                f"NOT_FOUND|query={query}|"
                                "El producto NO existe en el catálogo "
                                "(la búsqueda fuzzy + semántica ya corrió). "
                                "ESTE ITEM NO SE AGREGÓ. Acción requerida en "
                                "este mismo turno: 1) infiere la categoría más "
                                "probable a partir del contexto del mensaje y "
                                "del carrito (ej. en posición de bebida → "
                                "BEBIDAS); 2) llama list_category_products con "
                                "esa categoría para obtener las opciones "
                                "reales; 3) llama respond(kind='disambiguation', "
                                f"summary='No tenemos {query!r}, opciones "
                                "disponibles', facts=[...nombres y precios de "
                                "las alternativas...]). NO emitas items_added."
                            )
                        except Exception as exc:
                            logging.exception(
                                "[ORDER_AGENT] tool=%s raised: %s",
                                tool_name, exc,
                            )
                            result_str = (
                                f"Error al ejecutar {tool_name}: {exc}. "
                                "Pide disculpas al cliente y ofrécele alternativas."
                            )
                    tool_outputs[tool_name] = result_str
                    messages.append(
                        ToolMessage(content=result_str, tool_call_id=tool_id)
                    )

                if terminate:
                    break
            else:
                logging.warning(
                    "[ORDER_AGENT] max iterations reached without respond() "
                    "(executed=%s)",
                    executed_tools,
                )

            if envelope is None:
                # Either the model never called respond, or we hit max
                # iterations. Fall back to a chat envelope using the
                # model's last text as summary so the renderer has
                # something grounded to work with.
                envelope = {
                    "kind": "chat",
                    "summary": last_model_text or "Listo.",
                    "facts": [],
                }

            # Sanity guards on impossible envelopes — the model
            # occasionally emits ready_to_confirm/order_placed in
            # contexts where they make no sense, e.g. after
            # place_order clears the cart and a follow-up message gets
            # mis-routed back to order. Without these guards the
            # renderer happily produces a phantom confirm card from
            # leftover customer-DB data, confusing the customer
            # ("ya no estaba confirmado?").
            envelope = _guard_impossible_envelope(
                envelope=envelope,
                cart_was_empty_at_turn_start=not cart_has_items,
                tool_outputs=tool_outputs,
                wa_id=wa_id,
            )

            # Out-of-zone delivery → handoff to CS. Mirror of the
            # order_closed handoff pattern: order agent detects, CS
            # builds the deterministic redirect message. Keeps subsequent
            # turns ("ah ok gracias") in CS context where they belong.
            handoff_payload = _maybe_out_of_zone_handoff(envelope)
            if handoff_payload is not None:
                # Disarm any stale confirm-flag so a later genuine confirm
                # in CS handoff doesn't trip place_order.
                if awaiting_confirmation:
                    set_awaiting_confirmation(wa_id, str(business_id), False)
                tracer.end_run(
                    run_id, success=True,
                    latency_ms=(time.time() - start_time) * 1000,
                )
                logging.warning(
                    "[ORDER_AGENT] out_of_zone handoff → CS "
                    "city=%s phone=%s",
                    handoff_payload["context"].get("city"),
                    handoff_payload["context"].get("phone"),
                )
                return {
                    "agent_type": self.agent_type,
                    "message": "",
                    "state_update": {},
                    "handoff": handoff_payload,
                }

            rendered = render_response(
                envelope,
                business_context=business_context,
                last_user_message=message_body,
                wa_id=wa_id,
                tool_outputs=tool_outputs,
            )

            logging.warning(
                "[ORDER_TURN] wa_id=%s turn_id=%s tools=%s iterations=%d "
                "envelope_kind=%s response_type=%s latency_ms=%d",
                wa_id,
                message_id or "-",
                "|".join(executed_tools) or "-",
                iteration + 1,
                envelope.get("kind"),
                rendered.get("type"),
                int((time.time() - start_time) * 1000),
            )

            envelope_kind = (envelope.get("kind") or "").strip()
            is_confirm_prompt = envelope_kind == "ready_to_confirm"

            # Dispatch CTA directly via Twilio when the renderer asked
            # for one. We persist the rendered_body so the inbox UI and
            # planner history match what the customer actually saw.
            if rendered.get("type") == "cta":
                cta_sent = self._dispatch_cta(
                    wa_id=wa_id,
                    business_context=business_context,
                    rendered=rendered,
                )
                if cta_sent:
                    # Arm the state-machine interlock: place_order now
                    # refuses unless the next inbound turn is the user
                    # responding to this prompt.
                    set_awaiting_confirmation(wa_id, str(business_id), True)
                    self._persist(wa_id, business_id, rendered.get("body", ""))
                    tracer.end_run(
                        run_id, success=True,
                        latency_ms=(time.time() - start_time) * 1000,
                    )
                    return {
                        "agent_type": self.agent_type,
                        "message": "__SUPPRESS_SEND__",
                        "state_update": {"active_agents": ["order"]},
                    }
                logging.warning(
                    "[ORDER_AGENT] CTA send failed — falling back to text body"
                )
                # Fall through to text path with the rendered body.

            final_text = (rendered.get("body") or "").strip() or "Listo."

            # Symmetric flag write: arm on ready_to_confirm, clear on
            # everything else. Without this, a "no, cambia a Nequi" reply
            # leaves the flag stuck on True, and the next genuine
            # ready_to_confirm gets bypassed (or worse, place_order runs
            # without a fresh prompt).
            if is_confirm_prompt:
                set_awaiting_confirmation(wa_id, str(business_id), True)
            elif awaiting_confirmation:
                # Flag was on but this turn produced a different envelope
                # — disarm so the next confirm prompt re-arms cleanly.
                set_awaiting_confirmation(wa_id, str(business_id), False)

            self._persist(wa_id, business_id, final_text)
            tracer.end_run(
                run_id, success=True,
                latency_ms=(time.time() - start_time) * 1000,
            )
            return {
                "agent_type": self.agent_type,
                "message": final_text,
                "state_update": {"active_agents": ["order"]},
            }

        except Exception as exc:
            logging.exception("[ORDER_AGENT] error: %s", exc)
            tracer.end_run(
                run_id, success=False, error=str(exc),
                latency_ms=(time.time() - start_time) * 1000,
            )
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
        finally:
            reset_tool_context(token)

    # ── Internals ────────────────────────────────────────────────────

    def _persist(self, wa_id: str, business_id: str, text: str) -> None:
        if not text:
            return
        try:
            conversation_service.store_conversation_message(
                wa_id, text, "assistant", business_id=business_id,
            )
        except Exception as exc:
            logging.error("[ORDER_AGENT] persist failed: %s", exc)

    def _dispatch_cta(
        self,
        *,
        wa_id: str,
        business_context: Optional[Dict],
        rendered: Dict[str, Any],
    ) -> bool:
        """Send a Twilio Content Template. Returns True on success."""
        content_sid = rendered.get("content_sid") or ""
        variables = rendered.get("variables") or {}
        if not content_sid:
            return False
        try:
            from ..utils.whatsapp_utils import send_twilio_cta
            sent = send_twilio_cta(
                content_sid=content_sid,
                variables=variables,
                to=wa_id,
                business_context=business_context,
            )
            return sent is not None
        except Exception as exc:
            logging.error("[ORDER_AGENT] CTA dispatch raised: %s", exc)
            return False

    def _build_system_prompt(self, business_context: Optional[Dict]) -> str:
        from ..services.business_info_service import format_business_info_for_prompt
        biz_info = format_business_info_for_prompt(business_context)
        biz = (business_context or {}).get("business") or {}
        biz_name = (biz.get("name") or "el restaurante").strip()
        return _SYSTEM_PROMPT_TEMPLATE.format(
            business_name=biz_name,
            business_info=biz_info,
        )

    def _build_initial_messages(
        self,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_body: str,
        turn_ctx: Optional["TurnContext"] = None,
    ) -> List[Any]:
        if turn_ctx is None:
            turn_ctx = TurnContext()

        messages: List[Any] = [
            SystemMessage(content=self._build_system_prompt(business_context))
        ]

        # Unified context block — same wording every layer (router /
        # order / CS) sees. Includes order_state, cart contents,
        # delivery info already on file, awaiting_confirmation flag,
        # latest order status, and recent history. The model uses this
        # to understand "what's already done" so it doesn't redundantly
        # re-issue tools that target state that already matches.
        messages.append(
            SystemMessage(content=(
                "===== ESTADO Y HISTORIAL DEL TURNO =====\n"
                "(lo que YA pasó antes de este turno; NO repitas "
                "operaciones que ya están reflejadas aquí)\n\n"
                + render_for_prompt(turn_ctx)
                + "\n===== FIN DEL ESTADO ====="
            ))
        )

        # Behavioral hint when ready_to_confirm is armed. State above
        # tells the model "Esperando confirmación: SÍ" — this block tells
        # it what to *do* with that state. Kept separate from context so
        # behavioral guidance doesn't get confused with state.
        if turn_ctx.awaiting_confirmation:
            messages.append(
                SystemMessage(content=(
                    "ACCIÓN ESPERADA: En el turno anterior YA enviaste al "
                    "cliente la tarjeta/mensaje de confirmación. "
                    "- Si el mensaje del cliente es afirmativo "
                    "(\"Confirmar pedido\", \"si\", \"sí\", \"dale\", \"ok\", "
                    "\"listo\", \"confirma\", \"confirmo\", \"perfecto\"): "
                    "llama place_order DIRECTAMENTE. NO llames "
                    "respond(kind='ready_to_confirm') de nuevo — eso "
                    "enviaría una segunda tarjeta y confundiría al cliente. "
                    "- Si el cliente pide cambios, agrega items, o quiere "
                    "modificar datos: maneja la solicitud y NO uses "
                    "kind='ready_to_confirm' en este turno — el sistema "
                    "lo re-armará cuando vuelvas a estar listo."
                ))
            )

        # NOTE: We intentionally do NOT append `recent_history` as
        # individual HumanMessage/AIMessage objects here. The history
        # is rendered inside render_for_prompt above (labeled with
        # "usuario:" / "bot:" prefixes), so the model sees it as
        # context in the SystemMessage. This avoids the
        # "history-vs-current-turn" ambiguity where the model
        # re-processes prior user messages as if they were the current
        # input. Only the current turn appears as a HumanMessage.
        #
        # Operator-typed messages are visible inside the rendered
        # history block with the "operador (humano)" label.

        # Current turn — explicitly labeled so the model can never
        # confuse it with history. Even though it's the only
        # HumanMessage, the explicit marker makes the boundary
        # unmistakable when the model parses the prompt.
        messages.append(
            HumanMessage(content=(
                "[MENSAJE ACTUAL DEL CLIENTE — procesa SOLO este "
                "mensaje en este turno; los mensajes anteriores en "
                "CONTEXTO DEL TURNO son historial]\n\n"
                + message_body
            ))
        )
        return messages


def _unpack_tool_call(tc: Any) -> Tuple[str, Dict[str, Any], str]:
    """Normalize a tool_call entry from a ChatOpenAI response."""
    if isinstance(tc, dict):
        return tc.get("name", ""), tc.get("args") or {}, tc.get("id", "")
    return (
        getattr(tc, "name", ""),
        getattr(tc, "args", None) or {},
        getattr(tc, "id", ""),
    )


def _replace_history(
    ctx: "TurnContext", conversation_history: List[Dict],
) -> "TurnContext":
    """Return a copy of ``ctx`` with ``recent_history`` populated from
    a caller-supplied list of message dicts. Mirrors
    ``build_turn_context``'s history shaping (operator-remap, last 10).
    """
    from dataclasses import replace
    rendered: List[Tuple[str, str]] = []
    for entry in (conversation_history or [])[-10:]:
        role = (entry.get("role") or "").strip().lower()
        msg = (entry.get("content") or entry.get("message") or "").strip()
        if not role or not msg:
            continue
        agent_type = (entry.get("agent_type") or "").strip().lower()
        if role == "assistant" and agent_type == "operator":
            role = "operator"
        rendered.append((role, msg))
    last_assistant = ""
    for r, m in reversed(rendered):
        if r == "assistant":
            last_assistant = m
            break
    return replace(
        ctx,
        recent_history=tuple(rendered),
        last_assistant_message=last_assistant or ctx.last_assistant_message,
    )


def _read_cart_snapshot(wa_id: str, business_id: str) -> str:
    """Render the current cart as a compact, model-readable snapshot.

    Returns a few lines like:
        - 1x BARRACUDA - $28.000
        - 2x COCA-COLA - $11.000
        Subtotal: $39.000

    Empty string when there's nothing in the cart — the caller skips
    the runtime hint in that case so we don't waste prompt tokens on
    an empty section.
    """
    if not wa_id or not business_id:
        return ""
    try:
        from ..database.session_state_service import session_state_service
        from ..services import promotion_service
        from ..services.order_tools import _format_cart_display_lines, _format_price

        result = session_state_service.load(wa_id, business_id) or {}
        oc = (result.get("session") or {}).get("order_context") or {}
        items = oc.get("items") or []
        # Diagnostic: log the raw read so we can tell whether an empty
        # snapshot is "DB has nothing" vs "exception swallowed". Cheap
        # log, runs once per turn at start.
        logging.warning(
            "[ORDER_AGENT_DIAG] cart_read wa_id=%s items_count=%d "
            "state=%s delivery_present=%s",
            wa_id,
            len(items),
            oc.get("state"),
            bool((oc.get("delivery_info") or {}).get("address")),
        )
        if not items:
            return ""
        preview = promotion_service.preview_cart(business_id, items)
        lines = _format_cart_display_lines(preview["display_groups"])
        subtotal = preview.get("subtotal") or 0
        return "\n".join(lines + [f"Subtotal: {_format_price(subtotal)}"])
    except Exception as exc:
        logging.warning("[ORDER_AGENT] cart snapshot read failed: %s", exc)
        return ""


def _diag_cart_size(wa_id: str, business_id: str) -> int:
    """Count items in the canonical cart, for post-tool diagnostic logs."""
    if not wa_id or not business_id:
        return -1
    try:
        from ..database.session_state_service import session_state_service
        result = session_state_service.load(wa_id, business_id) or {}
        oc = (result.get("session") or {}).get("order_context") or {}
        items = oc.get("items") or []
        return len(items)
    except Exception:
        return -1


def _guard_impossible_envelope(
    envelope: Dict[str, Any],
    cart_was_empty_at_turn_start: bool,
    tool_outputs: Dict[str, str],
    wa_id: str,
) -> Dict[str, Any]:
    """Override envelopes that don't match the actual turn outcome.

    Two failure modes this catches:

    1. ``ready_to_confirm`` with no cart at turn start — happens when
       the router sends a non-order follow-up to v2 after a placed
       order. The renderer would otherwise build a "phantom" confirm
       card from leftover customer-DB data, confusing the customer.

    2. ``order_placed`` without a successful ``place_order`` tool
       result — happens when the model emits the kind after a guard
       refusal or other tool failure. Surfacing a fake receipt here
       is far worse than admitting the failure.

    Both get downgraded to ``chat`` with a helpful summary so the
    renderer produces a reasonable text response instead.
    """
    kind = (envelope.get("kind") or "").strip()

    if kind == "ready_to_confirm" and cart_was_empty_at_turn_start:
        # Detect the multi-intent failure mode: model saved delivery info
        # this turn (e.g. switched to pickup, captured a name) but never
        # called add_to_cart, so the confirm card would be for a phantom
        # order. Steer the chat reply toward "I have your data, what do
        # you want to order?" instead of the generic "no active order".
        sdi_output = (tool_outputs or {}).get("submit_delivery_info", "") or ""
        delivery_just_saved = "✅" in sdi_output and "Datos guardados" in sdi_output
        logging.warning(
            "[ORDER_AGENT] override_envelope kind=ready_to_confirm→chat "
            "reason=cart_empty wa_id=%s delivery_just_saved=%s",
            wa_id, delivery_just_saved,
        )
        if delivery_just_saved:
            summary = (
                "Caso especial: el cliente acaba de darte datos (modo de "
                "entrega y/o nombre) pero el carrito está vacío — el "
                "agente probablemente perdió el producto del mensaje "
                "multi-intención. Reconoce los datos guardados (si dijo "
                "su nombre, salúdalo por nombre) y pídele que te diga qué "
                "quiere pedir. NO emitas pregunta de confirmación, NO "
                "menciones \"pedido\" como si ya existiera. Tono: cálido, "
                "1-2 oraciones."
            )
        else:
            summary = (
                "El cliente envió un mensaje pero no hay un pedido activo "
                "(carrito vacío). Si pareces ver intención de ordenar, "
                "invítalo a decir qué quiere; si es una pregunta general "
                "(p.ej. sobre pagos, horarios, un pedido pasado), "
                "respóndele directamente sin pedir confirmación."
            )
        return {
            "kind": "chat",
            "summary": summary,
            "facts": envelope.get("facts") or [],
        }

    if kind == "order_placed":
        place_order_output = (tool_outputs or {}).get("place_order", "") or ""
        if "✅" not in place_order_output:
            logging.warning(
                "[ORDER_AGENT] override_envelope kind=order_placed→%s "
                "reason=no_successful_place_order wa_id=%s output=%r",
                "error" if place_order_output else "chat",
                wa_id,
                place_order_output[:120],
            )
            new_kind = "error" if place_order_output else "chat"
            summary = (
                place_order_output[:300]
                if place_order_output
                else "El pedido no se confirmó. Pide al cliente que lo intente de nuevo."
            )
            return {
                "kind": new_kind,
                "summary": summary,
                "facts": envelope.get("facts") or [],
            }

    return envelope


def _maybe_out_of_zone_handoff(envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """If the envelope is an ``out_of_zone:<city>`` redirect, return a
    dispatcher-style handoff payload pointing at customer_service with
    ``reason='out_of_zone'`` plus city/phone in context. Returns ``None``
    when the envelope isn't an out-of-zone redirect.

    The CS agent has a deterministic fast-path for this reason that
    builds the polished redirect message — no LLM, no hallucination.
    """
    kind = (envelope.get("kind") or "").strip()
    summary = (envelope.get("summary") or "").strip()
    if kind != "out_of_scope" or not summary.startswith("out_of_zone:"):
        return None
    city = summary[len("out_of_zone:"):].strip()
    phone = ""
    for fact in envelope.get("facts") or []:
        if not isinstance(fact, str):
            continue
        f = fact.strip()
        if f.lower().startswith("phone:"):
            phone = f.split(":", 1)[1].strip()
        elif f.lower().startswith("city:") and not city:
            city = f.split(":", 1)[1].strip()
    if not city or not phone:
        return None
    return {
        "to": "customer_service",
        "segment": f"[OUT_OF_ZONE] {city}",
        "context": {
            "reason": "out_of_zone",
            "city": city,
            "phone": phone,
        },
    }


def _envelope_from_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Lift the respond() args into a normalized envelope dict."""
    args = args or {}
    facts = args.get("facts")
    if facts is None:
        facts = []
    elif not isinstance(facts, list):
        facts = [str(facts)]
    return {
        "kind": (args.get("kind") or "chat").strip(),
        "summary": (args.get("summary") or "").strip(),
        "facts": [str(f) for f in facts],
    }
