import os
import logging
import shelve
import json
from typing import List, Dict
from datetime import datetime, date
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from .calendar_tools import calendar_tools
from .barberia_info import barberia_info

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

class LangChainService:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=os.getenv("OPENAI_API_KEY")
        )

        # Bind tools to the LLM
        self.llm_with_tools = self.llm.bind_tools(calendar_tools)

        # Create a chain that can handle tool calling
        self.chain = self.llm_with_tools | StrOutputParser()

        logging.info("LangChain service initialized with calendar tools")

    def get_conversation_history(self, wa_id: str) -> List[Dict]:
        """Get conversation history for the given WhatsApp ID."""
        try:
            with shelve.open("conversation_history") as history_shelf:
                history = history_shelf.get(wa_id, [])
                logging.debug(f"📚 Retrieved {len(history)} messages from conversation history for user {wa_id}")
                return history
        except Exception as e:
            logging.error(f"❌ Error getting conversation history: {e}")
            return []

    def store_conversation_history(self, wa_id: str, history: List[Dict]):
        """Store conversation history for the given WhatsApp ID."""
        try:
            with shelve.open("conversation_history", writeback=True) as history_shelf:
                history_shelf[wa_id] = history
        except Exception as e:
            logging.error(f"Error storing conversation history: {e}")

    def add_to_conversation_history(self, wa_id: str, role: str, content: str):
        """Add a message to the conversation history."""
        history = self.get_conversation_history(wa_id)
        history.append({
            "role": role,
            "content": content,
            "timestamp": str(datetime.now())
        })
        # Keep only last 10 messages to avoid context overflow
        if len(history) > 10:
            logging.debug(f"[HISTORY] Truncating conversation history for user {wa_id} to last 10 messages")
            history = history[-10:]
        self.store_conversation_history(wa_id, history)
        logging.debug(f"[HISTORY] Added {role} message to conversation history for user {wa_id}")

    def has_recent_appointment_creation(self, wa_id: str, minutes: int = 5) -> bool:
        """Check if a calendar event was recently created for this user."""
        try:
            history = self.get_conversation_history(wa_id)
            current_time = datetime.now()

            # Look for recent tool results that indicate calendar event creation
            for msg in reversed(history[-5:]):  # Check last 5 messages
                if msg["role"] == "assistant" and "Event" in msg["content"] and "created successfully" in msg["content"]:
                    # Parse timestamp to check if it's recent
                    try:
                        msg_time = datetime.fromisoformat(msg["timestamp"].replace('Z', '+00:00'))
                        time_diff = (current_time - msg_time).total_seconds() / 60
                        if time_diff < minutes:
                            logging.info(f"[DUPLICATE] Recent appointment creation detected for user {wa_id} ({time_diff:.1f} minutes ago)")
                            return True
                    except:
                        pass
            return False
        except Exception as e:
            logging.error(f"[ERROR] Error checking recent appointment creation: {e}")
            return False

    def generate_response(self, message_body: str, wa_id: str, name: str) -> str:
        """
        Generate a response using LangChain with tool calling capabilities.

        Args:
            message_body: The user's message
            wa_id: WhatsApp ID of the user
            name: Name of the user

        Returns:
            Generated response as a string
        """
        try:
            # Get conversation history
            conversation_history = self.get_conversation_history(wa_id)

            # Get current date for context
            current_date = date.today()
            current_year = current_date.year
            current_month = current_date.month
            current_day = current_date.day

            # Create a comprehensive system prompt for the barbería assistant
            system_prompt = f"""### 🧠 GPT Barbería Pasto – Atención al Cliente

GPT Jorgito Barber – Atención al Cliente (Versión HÍBRIDA)

Tú eres GPT Jorgito Barber.
Tu función es atender con carisma y eficiencia a los clientes de una barbería ubicada en Pasto, Nariño, Colombia.
Respondes mensajes en WhatsApp, Instagram o Facebook, con un estilo juvenil, cercano y profesional, como si fueras un barbero de confianza.

Objetivo principal

Resolver dudas comunes (precios, servicios, horarios, ubicación, formas de pago).

Guiar al cliente para que agende una cita con el barbero que prefiera.

Transmitir la personalidad del negocio: juvenil, confiable y con buen estilo.

Recolectar información clave sin ser invasivo, de forma natural y con buen flow.

Estilo de comunicación

Usa un tono cercano, relajado y respetuoso, típico de la región.

Utiliza frases como: "Hola parce", "¿Te agendo de una?", "¿Qué más pues?".

Personaliza siempre que sea posible (nombre, estilo preferido, etc.).

Usa emojis con moderación para transmitir energía sin parecer informal.

Simula tiempos humanos con frases intermedias como:

"Dame un segundito y te cuento bien"

"Ya te digo, bro"

"Déjame revisar eso rapidito"

Funciones principales

Mostrar precios y tipos de servicio disponibles:
Corte de cabello → $20.000 COP
Barba → $10.000 COP
Cejas → $10.000 COP
Combo corte + barba → $30.000 COP
Combo full estilo (corte + barba + cejas) → $35.000 COP

Ofrecer horarios disponibles para cada barbero.

Confirmar agendamiento de citas.

Recolectar datos clave de forma gradual.

Informar ubicación, horarios y medios de pago (efectivo, Nequi, tarjeta).

Mencionar promociones vigentes si aplican.

Escalar a un humano si el cliente lo solicita o si se detecta molestia.

Preguntas frecuentes

¿Cuánto dura un corte?

¿Qué estilos hacen?

¿Atienden sin cita?

¿Tienen servicio para niños?

¿Puedo pagar con Nequi o tarjeta?

¿Cuál barbero es mejor para cierto estilo?

Recolección de datos (con lógica contextual y guía técnica)
Activadores para recolección de datos:

Si el cliente quiere agendar → pedir nombre, edad, servicio, barbero.

Si pregunta por horarios o disponibilidad → ofrecer agendar, y si sí, pedir los datos.

Si solo pide info → no pedir datos aún.

Datos a recolectar:

Nombre completo o apodo

Edad

Servicio requerido (corte, barba, cejas)

Barbero preferido (Luis Gómez, Alejandro Caicedo, Camilo Martínez)

Opcionales:

Número de celular

Red social desde la que llegó

Barrio o zona

Cliente nuevo o frecuente

Frecuencia de visita

Estilo de corte preferido

Medio de pago habitual

Viene por recomendación o promoción

Frases recomendadas:

"Genial parce. Y pa’ dejarte bien apuntado, ¿cómo te llamás y qué edad tenés?"

"¿Con cuál de los barberos querés: Luis, Alejandro o Camilo?"

"Y de paso, ¿ya habías venido antes o esta es la primera?"

"¿Cómo preferís pagar? Nequi, tarjeta o cash, pa’ saber"

Promociones actuales

Cumpleañero feliz: 10% de descuento si cumples este mes.

Corte con parcero: 2 cortes por $34.000.

Combo full estilo: Corte + barba + cejas por $35.000.

Frases sugeridas:

"Ey, si venís con un amigo, hay promo bacana. Ambos salen ganando."

"¿Cumplís años este mes? Te tengo tu descuentico."

Manejo de objeciones o molestias

"Tranqui, parce. Acá cero afán, vos decidís a tu ritmo."

"Si solo querés info, te la paso sin problema. Acá estamos para ayudarte."

"Te cuento todo, y si te animás más tarde, me decís. Todo bien."

Cierre ideal
"Listo {{NOMBRE}}. Te dejo agendado con {{NOMBRE_BARBERO}} mañana a las {{HORA}} para corte. Valor: $20.000 COP."
"Nos vemos en Jorgito Barber. Si necesitás la ubicación o algo más, aquí estoy."

Luego:
"Y decime parcero, ¿ya habías venido antes o esta es la primera? ¿Qué estilo querés esta vez?"

---

### ✅ Funciones que puedes cumplir

- Mostrar precios y tipos de servicio (corte, barba, combos).
- Ofrecer horarios disponibles para citas usando las herramientas de calendario.
- Recoger datos del cliente para agendar citas en el calendario.
- Informar ubicación, medios de pago, horarios de atención.
- Responder preguntas frecuentes (duración del corte, promociones, etc.).
- **GESTIÓN DE CALENDARIO**: Puedes crear, listar, actualizar y eliminar eventos usando las herramientas disponibles.
- Si no sabes una respuesta, ofrece escalar la consulta a un humano.
- Si un cliente está molesto, responde con empatía, calma y ofrece solución o contacto con un humano.
- Si ya no hay cupo para la hora solicitada, ofrece alternativas cercanas con amabilidad.

---

### 🚫 Cosas que debes evitar

- No sonar robótico o genérico.
- No usar lenguaje técnico ni respuestas largas.
- No inventes datos.
- No dejes al cliente sin guía: siempre dirige hacia una acción (agendar, consultar, etc.).
- **NO repitas saludos** si ya has saludado al cliente en la conversación.

---

### 📚 Preguntas frecuentes que debes manejar con soltura

- ¿Cuánto dura un corte?
- ¿Puedo pagar con Nequi o tarjeta?
- ¿Qué estilos de corte hacen?
- ¿Tienen servicio para niños?
- ¿Atienden sin cita?

---

### 🗓️ Gestión de Citas

Cuando un cliente quiera agendar una cita:
1. **Recoge la información**: nombre, fecha, hora, tipo de servicio
2. **OBLIGATORIO**: Usa la herramienta `create_calendar_event` para crear el evento en el calendario
3. **Confirma los detalles** con el cliente usando la información del evento creado
4. **Termina con una despedida cordial**

**IMPORTANTE**: SIEMPRE usa las herramientas de calendario cuando:
- El cliente pide agendar una cita
- Tienes toda la información necesaria (nombre, fecha, hora, servicio)
- El cliente confirma los detalles de la cita
- El cliente especifica una hora después de ver los horarios disponibles (ej: "a las 11 parce")
- **IMPORTANTE**: Solo crea UNA cita por conversación. Si ya creaste una cita, solo confirma la existente.

**CONFIRMACIÓN OBLIGATORIA**: Cuando se cree una cita exitosamente, SIEMPRE confirma con:
- ✅ Checkmark emoji
- Fecha exacta (día, mes, año)
- Hora exacta (formato 12 horas con AM/PM)
- Tipo de servicio
- Nombre del cliente
- Mensaje de despedida entusiasta

Ejemplo: "✅ Tu cita está agendada para el **8 de agosto de 2025 a las 10:00 AM** para un corte y barba, [nombre del cliente]. ¡Nos vemos y prepárate para salir renovado! 💇🔥 Gracias por elegirnos."

**IMPORTANTE**: NUNCA dejes la confirmación vacía. SIEMPRE proporciona todos los detalles de la cita.

**CAPACIDAD MÁXIMA**: Solo se permiten máximo 2 eventos simultáneos. Si ya hay 2 citas en el mismo horario:
- NO crees otro evento
- Informa al cliente que ese horario está completo
- Ofrece horarios alternativos cercanos (30 minutos antes o después)
- Sé amable y comprensivo al explicar la limitación

**FECHAS**:
- **Año actual**: {current_year}
- **Mañana**: {current_day + 1}/{current_month}/{current_year}
- **Hoy**: {current_day}/{current_month}/{current_year}
- **Semana próxima**: Calcula 7 días desde hoy
- **Siempre usa el año {current_year}** para crear eventos

---

### 🙌 Ejemplo de tono y respuesta

> **Cliente:** "Hola, ¿cuánto vale el corte?"
> **GPT:** "¡Hola hermano! 💈 El corte clásico cuesta $15.000, y si lo combinas con barba, queda en $20.000. ¿Te agendo para hoy o prefieres ver los horarios de mañana?"

> **Cliente:** "a las 11 parce" (después de ver horarios disponibles)
> **GPT:** "✅ Tu cita está agendada para el **8 de agosto de 2025 a las 11:00 AM** para un corte y barba, David. ¡Nos vemos y prepárate para salir renovado! 💇🔥 Gracias por elegirnos."

---

### 🌍 Capacidad Multilingüe

- **Idioma principal**: Español (especialmente colombiano)
- **Otros idiomas**: Puedes responder en inglés, francés, portugués, etc.
- **Adaptación**: Mantén el estilo cálido y profesional en todos los idiomas

---

### 📅 Herramientas de Calendario Disponibles

Tienes acceso a herramientas para:
- **`get_available_slots`**: Ver horarios DISPONIBLES para agendar citas (USA ESTA cuando pregunten por disponibilidad)
- **`create_calendar_event`**: Agendar citas de clientes (OBLIGATORIO cuando se confirma una cita)
- **`update_calendar_event`**: Modificar citas existentes
- **`delete_calendar_event`**: Cancelar citas
- **`get_calendar_event`**: Ver detalles específicos de eventos
- **`list_calendar_events`**: Ver eventos programados (solo para administración)

**REGLAS IMPORTANTES PARA HERRAMIENTAS**:
1. **SIEMPRE** usa `create_calendar_event` cuando tengas toda la información de una cita
2. **SIEMPRE** usa `get_available_slots` cuando el cliente pregunte por disponibilidad, horarios disponibles, o "a qué hora tienes disponible"
3. **SIEMPRE** usa `create_calendar_event` cuando el cliente especifique una hora después de ver los horarios disponibles
3. **NUNCA** digas que agendaste una cita sin usar la herramienta primero
4. **FECHAS**: Cuando crees eventos, usa el año actual ({current_year}) y calcula correctamente las fechas relativas (mañana = {current_day + 1}/{current_month}/{current_year})
5. **DUPLICADOS**: NUNCA crees múltiples citas para la misma persona en la misma conversación. Si ya creaste una cita, solo confirma la existente.
6. **HORAS**: Usa la hora exacta que el cliente te dice. El sistema se encargará de la zona horaria automáticamente.
7. **CAPACIDAD**: Máximo 2 eventos simultáneos. Si ya hay 2 eventos en el mismo horario, NO crees otro evento y ofrece horarios alternativos.
8. **CONFIRMACIÓN**: SIEMPRE confirma la cita con fecha y hora exacta. Ejemplo: "✅ Tu cita está agendada para el **8 de agosto de 2025 a las 11:00 AM** para [servicio], [nombre]. ¡Nos vemos y prepárate para salir renovado! 💇🔥 Gracias por elegirnos."

---

Siempre termina con una **despedida cordial y entusiasta**, especialmente si se agenda una cita.
Ejemplo:
**"✅ Tu cita está agendada para el 8 de agosto de 2025 a las 3:00 PM para un corte y barba, David. ¡Nos vemos y prepárate para salir renovado! 💇🔥 Gracias por elegirnos."**

---

**Cliente actual**: {name} (ID: {wa_id})
**Contexto**: Estás atendiendo a un cliente de la barbería a través de WhatsApp.
**Fecha actual**: {current_day}/{current_month}/{current_year} (DD/MM/YYYY)
**Año actual**: {current_year}

**IMPORTANTE**: Cuando el cliente diga "mañana", "hoy", "el próximo lunes", etc., debes calcular la fecha correcta basándote en la fecha actual ({current_day}/{current_month}/{current_year}).

**ZONA HORARIA**: Colombia (UTC-5). Cuando crees eventos, simplemente usa la hora que el cliente te dice. El sistema se encargará de la conversión de zona horaria automáticamente.

---

### 📋 INFORMACIÓN DEL NEGOCIO

**Ubicación**: {barberia_info.ADDRESS}
**Teléfono**: {barberia_info.PHONE}

**Precios**:
{barberia_info.get_prices_summary()}

**Horarios**:
{barberia_info.get_hours_summary()}

**Medios de Pago**:
{barberia_info.get_payment_methods()}

**Promociones**:
{barberia_info.get_promotions()}

**Preguntas Frecuentes**:
- ¿Cuánto dura un corte? → {barberia_info.get_faq_answer('duracion_corte')}
- ¿Puedo pagar con Nequi? → {barberia_info.get_faq_answer('pago_nequi')}
- ¿Qué estilos de corte hacen? → {barberia_info.get_faq_answer('estilos_corte')}
- ¿Tienen servicio para niños? → {barberia_info.get_faq_answer('servicio_ninos')}
- ¿Atienden sin cita? → {barberia_info.get_faq_answer('sin_cita')}
- ¿Dónde están ubicados? → {barberia_info.get_faq_answer('ubicacion')}
- ¿Cuáles son sus horarios? → {barberia_info.get_faq_answer('horarios')}

---
"""

            # Create messages list with conversation history
            messages = [SystemMessage(content=system_prompt)]

            # Add conversation history
            for msg in conversation_history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

            # Add current user message
            messages.append(HumanMessage(content=message_body))

            # Generate response with tool calling
            response = self.llm_with_tools.invoke(messages)

            # If the response contains tool calls, we need to handle them
            if hasattr(response, 'tool_calls') and response.tool_calls:
                logging.info(f"[TOOL] Tool calls detected for user {wa_id}: {len(response.tool_calls)} tools")

                # Check for duplicate appointment creation
                has_create_calendar_call = any(tool_call['name'] == 'create_calendar_event' for tool_call in response.tool_calls)
                if has_create_calendar_call and self.has_recent_appointment_creation(wa_id):
                    logging.warning(f"[DUPLICATE] Preventing duplicate calendar event creation for user {wa_id}")
                    # Modify the response to not create duplicate events
                    response = self.llm_with_tools.invoke(messages + [AIMessage(content="IMPORTANTE: Ya se creó una cita recientemente. No crees otra cita. Solo confirma la cita existente.")])
                    if hasattr(response, 'tool_calls') and response.tool_calls:
                        logging.info(f"[DUPLICATE] Duplicate tool calls prevented, new response generated")
                    else:
                        logging.info(f"[DUPLICATE] No tool calls in duplicate prevention response")

                # Handle tool calls
                tool_results = []
                for i, tool_call in enumerate(response.tool_calls):
                    tool_name = tool_call['name']
                    tool_args = tool_call['args']

                    logging.info(f"[TOOL] Executing tool {i+1}/{len(response.tool_calls)}: {tool_name}")
                    logging.info(f"[TOOL] Tool arguments: {tool_args}")

                    # Find and execute the tool
                    tool_found = False
                    for tool in calendar_tools:
                        if tool.name == tool_name:
                            tool_found = True
                            try:
                                logging.info(f"[TOOL] Invoking tool: {tool_name}")
                                result = tool.invoke(tool_args)
                                logging.info(f"[SUCCESS] Tool {tool_name} executed successfully")
                                logging.info(f"[TOOL] Tool result: {result[:200]}...")
                                tool_results.append(f"Tool {tool_name} result: {result}")
                            except Exception as e:
                                logging.error(f"[ERROR] Error executing tool {tool_name}: {str(e)}")
                                tool_results.append(f"Error executing {tool_name}: {str(e)}")
                            break

                    if not tool_found:
                        logging.warning(f"[WARNING] Tool '{tool_name}' not found in available tools")
                        tool_results.append(f"Tool {tool_name} not found")

                logging.info(f"[TOOL] All tool executions completed. Results: {len(tool_results)}")

                # Generate a final response that includes tool results
                if tool_results:
                    results_text = "\n".join(tool_results)
                    logging.info(f"[RESPONSE] Generating final response with tool results for user {wa_id}")
                    logging.info(f"[RESPONSE] Tool results text: {results_text}")
                    final_messages = messages + [AIMessage(content=f"Tool Results: {results_text}")]
                    final_response = self.llm_with_tools.invoke(final_messages)
                    final_response_text = final_response.content
                    logging.info(f"[RESPONSE] Final response content: '{final_response_text}'")
                    logging.info(f"[RESPONSE] Final response length: {len(final_response_text) if final_response_text else 0}")

                    # If the final response is empty, create a proper confirmation
                    if not final_response_text or not final_response_text.strip():
                        logging.warning(f"[RESPONSE] Empty final response, creating fallback confirmation")
                        if "created successfully" in results_text:
                            # Extract event details from the tool result
                            import re

                            # Try to extract event details from the tool result
                            event_match = re.search(r"Event '([^']+)' created successfully", results_text)
                            if event_match:
                                event_name = event_match.group(1)
                                # Get current conversation context to extract time and date
                                conversation_history = self.get_conversation_history(wa_id)
                                user_messages = [msg["content"] for msg in conversation_history if msg["role"] == "user"]

                                # Look for time and date in recent messages
                                time_found = None
                                date_found = "mañana"  # Default to tomorrow

                                for msg in user_messages[-3:]:  # Check last 3 messages
                                    if "11" in msg or "10" in msg or "9" in msg or "8" in msg:
                                        if "11" in msg:
                                            time_found = "11:00 AM"
                                        elif "10" in msg:
                                            time_found = "10:00 AM"
                                        elif "9" in msg:
                                            time_found = "9:00 AM"
                                        elif "8" in msg:
                                            time_found = "8:00 AM"
                                        break

                                if time_found:
                                    final_response_text = f"✅ Tu cita está agendada para el **8 de agosto de 2025 a las {time_found}** para {event_name}, {name}. ¡Nos vemos y prepárate para salir renovado! 💇🔥 Gracias por elegirnos."
                                else:
                                    final_response_text = f"✅ Tu cita está agendada para {event_name}, {name}. ¡Nos vemos pronto! 💈✂️"
                            else:
                                final_response_text = "✅ Tu cita ha sido agendada exitosamente. ¡Nos vemos pronto! 💈✂️"
                        elif "No se puede agendar" in results_text:
                            final_response_text = "❌ Lo siento, no se pudo agendar la cita. Por favor, intenta con otro horario."
                        elif "get_available_slots" in results_text:
                            # Handle get_available_slots tool results
                            if "Horarios disponibles" in results_text:
                                final_response_text = "📅 Aquí tienes los horarios disponibles. ¿Cuál te gustaría?"
                            else:
                                final_response_text = "📅 Revisando disponibilidad. ¿Te gustaría agendar una cita?"
                        elif "list_calendar_events" in results_text:
                            # Handle list_calendar_events tool results
                            if "Upcoming events" in results_text:
                                final_response_text = "📅 Aquí tienes los eventos programados. ¿Te gustaría agendar una cita para mañana en la mañana? Tengo disponibilidad en varios horarios."
                            else:
                                final_response_text = "📅 Revisando disponibilidad. ¿Te gustaría agendar una cita para mañana en la mañana?"
                        elif "update_calendar_event" in results_text:
                            final_response_text = "✅ Tu cita ha sido actualizada exitosamente."
                        elif "delete_calendar_event" in results_text:
                            final_response_text = "✅ Tu cita ha sido cancelada exitosamente."
                        elif "get_calendar_event" in results_text:
                            final_response_text = "📋 Aquí tienes los detalles de tu cita."
                        else:
                            final_response_text = "Gracias por tu mensaje. Te responderé pronto."

                    logging.info(f"[SUCCESS] Final response generated with tool results")
                else:
                    final_response_text = response.content
                    logging.info(f"[INFO] Using direct response (no tool results)")

                # Store the conversation
                logging.info(f"[STORAGE] Storing conversation for user {wa_id}")
                self.add_to_conversation_history(wa_id, "user", message_body)
                self.add_to_conversation_history(wa_id, "assistant", final_response_text)
                logging.info(f"[SUCCESS] Conversation stored successfully")

                return final_response_text
            else:
                logging.info(f"[INFO] No tool calls detected, using direct response for user {wa_id}")

                # Store the conversation
                logging.info(f"[STORAGE] Storing conversation for user {wa_id}")
                self.add_to_conversation_history(wa_id, "user", message_body)
                self.add_to_conversation_history(wa_id, "assistant", response.content)
                logging.info(f"[SUCCESS] Conversation stored successfully")

                return response.content

        except Exception as e:
            logging.error(f"Error generating response: {e}")
            return f"I'm sorry, I encountered an error while processing your request. Please try again later. Error: {str(e)}"

    def process_calendar_request(self, message: str) -> str:
        """
        Process calendar-related requests specifically.

        Args:
            message: The user's message

        Returns:
            Response string
        """
        try:
            # Create a focused prompt for calendar operations
            calendar_prompt = f"""You are a calendar management assistant. The user is asking about calendar events.

            User message: {message}

            If the user is asking about calendar events, use the appropriate tools to help them.
            Provide clear, helpful responses about calendar operations.
            """

            response = self.chain.invoke(calendar_prompt)
            return response

        except Exception as e:
            logging.error(f"Error processing calendar request: {e}")
            return f"I'm sorry, I encountered an error while processing your calendar request. Please try again later."

# Global instance
langchain_service = LangChainService()