#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool, Float32
from geometry_msgs.msg import Twist
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
import websockets
import asyncio
import json
import threading
from datetime import datetime
import signal
import sys
import re
import time

class AdvancedHAWebSocketBridge(Node):
    def __init__(self):
        super().__init__('advanced_ha_websocket_bridge')
        
        # Configurazione Home Assistant (personalizza qui o leggi da file)
        self.ha_config = {
            'url': '192.168.1.45',
            'port': 8123,
            'token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIwZTU4MTA4OWEyZjk0YTk3OGQ2NWQyOTY5YWM3MjEyOSIsImlhdCI6MTc2MDI5ODE2MCwiZXhwIjoyMDc1NjU4MTYwfQ.8dymD3-cId-1v7kVIuvqXiZCrOF9ONVMvIh2_cGXgvo',
            'reconnect_delay': 5
        }
        
        self.websocket = None
        self.is_connected = False
        self.loop = None
        self.shutdown_requested = False
        
        # Metriche di performance
        self.cpu_usage = 0.0
        self.memory_usage = 0.0
        self.cpu_temperature = 0.0
        self.device_model = "Unknown"
        
        # Inizializza ROS2 communications
        self.init_ros_communications()
        
        # Avvia gestione connessione in thread separato
        self.connection_thread = threading.Thread(target=self.connection_manager)
        self.connection_thread.daemon = True
        self.connection_thread.start()
        
        # Timer per verificare la connessione
        self.connection_check_timer = self.create_timer(10.0, self.check_connection_status)
        
        # Timer per verificare se stiamo ricevendo dati di performance
        self.performance_check_timer = self.create_timer(30.0, self.check_performance_data)
        self.last_performance_time = datetime.now()
        
        self.get_logger().info("🤖 Nodo Home Assistant Bridge inizializzato")

    def init_ros_communications(self):
        """Inizializza publisher e subscriber ROS2"""
        # Publisher per comandi HA → ROS2
        self.ha_light_pub = self.create_publisher(String, 'ha/lights', 10)
        self.ha_switch_pub = self.create_publisher(Bool, 'ha/switches', 10)
        self.ha_button_pub = self.create_publisher(String, 'ha/buttons', 10)
        
        # Publisher per movimento robot
        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        
        # Subscriber per dati ROS2 → HA
        self.create_subscription(Float32, '/sensors/temperature', self.sensor_callback, 10)
        self.create_subscription(String, '/robot/state', self.robot_state_callback, 10)
        
        # Subscriber per le metriche di performance
        self.create_subscription(
            DiagnosticStatus, 
            '/system/performance', 
            self.performance_callback, 
            10
        )

    def _extract_number(self, s):
        """
        Estrae il primo numero (int/float) da una stringa.
        Supporta formati come '30', '30.5', '30%', '30 °C'.
        Ritorna float o solleva ValueError.
        """
        if s is None:
            raise ValueError("None value")
        s = str(s).strip()
        m = re.search(r"[-+]?\d+(\.\d+)?", s)
        if not m:
            raise ValueError(f"No numeric content in '{s}'")
        return float(m.group(0))

    def performance_callback(self, msg):
        """Callback per le metriche di performance dal nodo performance_monitor"""
        try:
            # Aggiorna timestamp ultimi dati ricevuti
            self.last_performance_time = datetime.now()
            
            # DEBUG: Log di tutti i valori ricevuti
            self.get_logger().info(f"📊 Ricevuti {len(msg.values)} valori di performance:")
            
            # Per debug dettagliato
            # self.get_logger().debug(f"Full DiagnosticStatus: {msg}")
            
            for kv in msg.values:
                key = (kv.key or "").strip()
                val = (kv.value or "").strip()
                self.get_logger().info(f"  - {key}: {val}")
                
                # Estrai i valori con match su substring (uso strip sulle chiavi)
                try:
                    if "CPU Usage" in key:
                        self.cpu_usage = self._extract_number(val)
                        self.get_logger().info(f"✅ CPU Usage trovato: {self.cpu_usage}")
                    elif "Memory Usage" in key:
                        self.memory_usage = self._extract_number(val)
                        self.get_logger().info(f"✅ Memory Usage trovato: {self.memory_usage}")
                    elif "CPU Temperature" in key:
                        self.cpu_temperature = self._extract_number(val)
                        self.get_logger().info(f"✅ CPU Temperature trovato: {self.cpu_temperature}")
                    elif "Device Model" in key:
                        # Device Model è testo
                        self.device_model = val
                        self.get_logger().info(f"✅ Device Model trovato: {self.device_model}")
                    else:
                        self.get_logger().debug(f"⚪ Key non gestita: '{key}'")
                except ValueError as ve:
                    self.get_logger().error(f"❌ Parsing fallito per '{key}': '{val}' -> {ve}")
                except Exception as e:
                    self.get_logger().error(f"❌ Errore gestione '{key}': {e}")
            
            # Log dei valori estratti
            self.get_logger().info(
                f"📊 Performance estratte - CPU: {self.cpu_usage}, "
                f"Mem: {self.memory_usage}, Temp: {self.cpu_temperature}, "
                f"Device: {self.device_model}"
            )
            
            # Invia i dati a Home Assistant
            if self.loop and self.is_connected and not self.shutdown_requested:
                asyncio.run_coroutine_threadsafe(
                    self.update_performance_sensors(), 
                    self.loop
                )
            else:
                self.get_logger().warning("⚠️  Non connesso a HA, salto invio dati performance")
            
        except Exception as e:
            self.get_logger().error(f"❌ Errore processing performance data: {str(e)}")

    def check_performance_data(self):
        """Verifica se stiamo ricevendo dati di performance"""
        time_since_last_update = (datetime.now() - self.last_performance_time).total_seconds()
        if time_since_last_update > 60:
            self.get_logger().debug(f"⚠️  Nessun dato di performance ricevuto da {time_since_last_update:.0f} secondi")

    async def update_performance_sensors(self):
        """Aggiorna i sensori di performance in Home Assistant"""
        if not self.is_connected or not self.websocket:
            self.get_logger().warning("⚠️  Non connesso a HA, salto aggiornamento performance")
            return
        try:
            self.get_logger().info(
                f"📤 Invio a HA - CPU: {self.cpu_usage}, "
                f"Mem: {self.memory_usage}, Temp: {self.cpu_temperature}, Device: {self.device_model}"
            )
            
            # Aggiorna CPU Usage
            ok_cpu = await self.update_ha_entity("input_number.ros_cpu_usage", self.cpu_usage)
            # Aggiorna Memory Usage  
            ok_mem = await self.update_ha_entity("input_number.ros_memory_usage", self.memory_usage)
            # Aggiorna CPU Temperature
            ok_temp = await self.update_ha_entity("input_number.ros_cpu_temperature", self.cpu_temperature)
            # Aggiorna device model (se disponibile)
            ok_dev = False
            if self.device_model and self.device_model != "Unknown":
                ok_dev = await self.update_ha_entity("input_text.ros_device_model", self.device_model)
            
            self.get_logger().info(
                f"✅ Risultati invio - CPU: {'OK' if ok_cpu else 'ERR'}, "
                f"Mem: {'OK' if ok_mem else 'ERR'}, Temp: {'OK' if ok_temp else 'ERR'}, Dev: {'OK' if ok_dev else 'SKIP/ERR'}"
            )
        except Exception as e:
            self.get_logger().error(f"❌ Errore aggiornamento sensori performance HA: {str(e)}")

    def check_connection_status(self):
        """Verifica periodicamente lo stato della connessione"""
        if not self.is_connected and not self.shutdown_requested:
            self.get_logger().debug("🔌 Connessione HA persa - tentativo di riconnessione...")

    def connection_manager(self):
        """Gestisce la connessione in thread separato"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.connect_to_ha())
        except Exception as e:
            self.get_logger().error(f"❌ Errore nel connection manager: {e}")
        finally:
            # provare a chiudere pulito
            try:
                self.loop.close()
            except Exception:
                pass

    async def connect_to_ha(self):
        """Gestisce connessione e riconnessione a HA"""
        while rclpy.ok() and not self.shutdown_requested:
            try:
                uri = f"ws://{self.ha_config['url']}:{self.ha_config['port']}/api/websocket"
                self.get_logger().debug(f"🔄 Tentativo di connessione a {uri}")
                
                # Timeout più lungo per la connessione iniziale
                self.websocket = await websockets.connect(
                    uri, 
                    ping_interval=20, 
                    ping_timeout=10,
                    close_timeout=5
                )
                
                # Handshake iniziale
                hello_msg = await self.websocket.recv()
                self.get_logger().info(f"🤝 Handshake: {hello_msg}")
                
                # Autenticazione
                await self.authenticate()
                self.is_connected = True
                
                # Aggiorna stato a "Connesso"
                await self.update_ha_entity("input_text.robot_state", "🟢 Connesso")
                
                # Invia dati iniziali di performance
                await self.update_performance_sensors()
                
                self.get_logger().info("✅ Connesso e autenticato a Home Assistant")
                
                # Inizia ad ascoltare eventi
                await self.start_event_listener()
                
            except Exception as e:
                self.is_connected = False
                if not self.shutdown_requested:
                    self.get_logger().debug(f"❌ Errore connessione: {str(e)}")
                    self.get_logger().debug(f"🔄 Ritento connessione in {self.ha_config['reconnect_delay']} secondi...")
                    await asyncio.sleep(self.ha_config['reconnect_delay'])

    async def authenticate(self):
        """Autenticazione con Home Assistant"""
        auth_msg = {
            "type": "auth",
            "access_token": self.ha_config['token']
        }
        await self.websocket.send(json.dumps(auth_msg))
        
        response = await self.websocket.recv()
        result = json.loads(response)
        
        if result.get("type") == "auth_ok":
            self.get_logger().info("✅ Autenticazione riuscita!")
        else:
            error = result.get("message", "Errore sconosciuto")
            raise Exception(f"Autenticazione fallita: {error}")

    async def start_event_listener(self):
        """Ascolta eventi da Home Assistant"""
        # Sottoscrizione a tutti gli eventi di stato
        subscribe_msg = {
            "id": 1,
            "type": "subscribe_events",
            "event_type": "state_changed"
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        self.get_logger().info("📡 In ascolto di eventi Home Assistant...")
        
        while self.is_connected and rclpy.ok() and not self.shutdown_requested:
            try:
                message = await asyncio.wait_for(
                    self.websocket.recv(), 
                    timeout=30.0
                )
                await self.process_ha_event(message)
                
            except asyncio.TimeoutError:
                # Timeout normale - controlla se siamo ancora connessi
                continue
            except websockets.exceptions.ConnectionClosed:
                self.get_logger().warning("🔌 Connessione WebSocket chiusa")
                self.is_connected = False
                break
            except Exception as e:
                if not self.shutdown_requested:
                    self.get_logger().error(f"❌ Errore ricezione messaggio: {str(e)}")
                self.is_connected = False
                break

    async def process_ha_event(self, message):
        """Processa eventi da Home Assistant"""
        try:
            data = json.loads(message)
            
            if data.get("type") == "event":
                event = data.get("event", {})
                event_data = event.get("data", {})
                # entity_id può essere singolo ID; new_state potrebbe mancare
                entity_id = event_data.get("entity_id") or event_data.get("entity_id", None)
                new_state = None
                try:
                    new_state = event_data.get("new_state", {}).get("state", None)
                except Exception:
                    new_state = None
                
                self.get_logger().info(f"📨 Evento HA: {entity_id} -> {new_state}")
                
                # Gestisci diversi tipi di entità
                if entity_id:
                    await self.handle_entity_event(entity_id, new_state)
                else:
                    self.get_logger().debug("Evento senza entity_id, skip")
                
        except Exception as e:
            self.get_logger().error(f"❌ Errore processamento evento: {str(e)}")

    async def handle_entity_event(self, entity_id, state):
        """Gestisce eventi per specifiche entità"""
        # Bottoni di movimento
        if entity_id.startswith("input_button."):
            await self.handle_movement_button(entity_id, state)
        
        # Luci
        elif entity_id.startswith("light."):
            msg = String()
            msg.data = f"{entity_id}:{state}"
            self.ha_light_pub.publish(msg)
        
        # Interruttori
        elif entity_id.startswith("switch."):
            msg = Bool()
            msg.data = (str(state).lower() == "on")
            self.ha_switch_pub.publish(msg)
        
        # Sensori
        elif entity_id.startswith("sensor."):
            self.get_logger().info(f"📊 Sensore {entity_id}: {state}")

    async def handle_movement_button(self, entity_id, state):
        """Gestisce i bottoni di movimento"""
        try:
            self.get_logger().info(f"🎯 Bottone premuto: {entity_id}")
            
            # Crea messaggio di movimento
            twist_msg = Twist()
            
            # Configura velocità in base al bottone
            if "robot_forward" in entity_id:
                twist_msg.linear.x = 0.5  # Avanti
                self.get_logger().info("🚀 Comando: AVANTI")
                await self.update_ha_entity("input_text.robot_state", "🟢 Connesso - AVANTI")
                
            elif "robot_backward" in entity_id:
                twist_msg.linear.x = -0.5  # Indietro
                self.get_logger().info("🔙 Comando: INDIETRO")
                await self.update_ha_entity("input_text.robot_state", "🟢 Connesso - INDIETRO")
                
            elif "robot_left" in entity_id:
                twist_msg.angular.z = 0.5  # Sinistra
                self.get_logger().info("↩️ Comando: SINISTRA")
                await self.update_ha_entity("input_text.robot_state", "🟢 Connesso - SINISTRA")
                
            elif "robot_right" in entity_id:
                twist_msg.angular.z = -0.5  # Destra
                self.get_logger().info("↪️ Comando: DESTRA")
                await self.update_ha_entity("input_text.robot_state", "🟢 Connesso - DESTRA")
                
            elif "robot_stop" in entity_id:
                twist_msg.linear.x = 0.0  # Stop
                twist_msg.angular.z = 0.0
                self.get_logger().info("🛑 Comando: STOP")
                await self.update_ha_entity("input_text.robot_state", "🟢 Connesso - STOP")
            
            # Invia il comando di movimento
            self.cmd_vel_pub.publish(twist_msg)
            
        except Exception as e:
            self.get_logger().error(f"❌ Errore gestione movimento: {str(e)}")

    def sensor_callback(self, msg):
        """Callback per dati sensoriali ROS2 → HA"""
        if self.loop and self.is_connected and not self.shutdown_requested:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.update_ha_entity("input_text.ros_temperature", f"{msg.data}°C"), 
                    self.loop
                )
            except Exception:
                pass  # Ignora errori durante shutdown

    def robot_state_callback(self, msg):
        """Callback per stato robot ROS2 → HA"""
        if self.loop and self.is_connected and not self.shutdown_requested:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.update_ha_entity("input_text.robot_state", f"🟢 Connesso - {msg.data}"), 
                    self.loop
                )
            except Exception:
                pass  # Ignora errori durante shutdown

    async def update_ha_entity(self, entity_id, state):
        """Aggiorna un'entità in Home Assistant - versione che ritorna True/False"""
        if not self.is_connected or not self.websocket or self.shutdown_requested:
            self.get_logger().debug(f"Skip update {entity_id} (not connected/shutting down)")
            return False
            
        try:
            # Determina il dominio in base al tipo di entità
            call_id = int(time.time() * 1000) % 2**31
            if entity_id.startswith("input_text."):
                service_call = {
                    "id": call_id,
                    "type": "call_service",
                    "domain": "input_text",
                    "service": "set_value",
                    "service_data": {
                        "entity_id": entity_id,
                        "value": str(state)
                    }
                }
                await self.websocket.send(json.dumps(service_call))
                self.get_logger().debug(f"📤 Aggiornato HA input_text: {entity_id} = {state}")
                return True
                
            elif entity_id.startswith("input_number."):
                try:
                    val = float(state)
                except Exception:
                    self.get_logger().error(f"❌ Valore non convertibile in float per {entity_id}: {state}")
                    return False

                service_call = {
                    "id": call_id,
                    "type": "call_service",
                    "domain": "input_number", 
                    "service": "set_value",
                    "service_data": {
                        "entity_id": entity_id,
                        "value": val
                    }
                }
                await self.websocket.send(json.dumps(service_call))
                self.get_logger().debug(f"📤 Aggiornato HA input_number: {entity_id} = {val}")
                return True
            else:
                self.get_logger().warning(f"Tipo di entità non supportato: {entity_id}")
                return False
                
        except Exception as e:
            if not self.shutdown_requested:
                self.get_logger().error(f"❌ Errore aggiornamento HA {entity_id}: {str(e)}")
            return False
        
    async def graceful_shutdown(self):
        """Chiude la connessione in modo pulito"""
        self.shutdown_requested = True
        self.is_connected = False
        
        self.get_logger().info("🛑 Avvio shutdown pulito...")
        
        try:
            # Aggiorna stato a "Disconnesso"
            if self.websocket:
                await self.update_ha_entity("input_text.robot_state", "🔴 Disconnesso")
                await asyncio.sleep(0.5)  # Dai tempo per inviare il messaggio
                
                # Chiudi la connessione WebSocket
                try:
                    await self.websocket.close()
                except Exception:
                    pass
                self.websocket = None
                
        except Exception as e:
            self.get_logger().error(f"❌ Errore durante shutdown: {e}")
        
        self.get_logger().info("✅ Nodo HA Bridge spento correttamente")

    def destroy_node(self):
        """Override del destroy_node per shutdown pulito"""
        self.get_logger().info("🔧 Distruzione nodo...")
        self.shutdown_requested = True
        
        if self.loop and self.loop.is_running():
            # Esegui shutdown pulito nel thread asyncio
            future = asyncio.run_coroutine_threadsafe(self.graceful_shutdown(), self.loop)
            try:
                future.result(timeout=10.0)  # Aspetta max 10 secondi
            except Exception:
                self.get_logger().warning("Timeout durante shutdown")
        
        super().destroy_node()

def main(args=None):
    """Funzione principale richiesta da ROS2"""
    rclpy.init(args=args)
    
    node = None
    try:
        node = AdvancedHAWebSocketBridge()
        # Cattura segnali per shutdown pulito
        def _sig_handler(sig, frame):
            if node:
                node.get_logger().info("⏹️ Segnale di terminazione ricevuto")
                try:
                    node.destroy_node()
                except Exception:
                    pass
            rclpy.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        rclpy.spin(node)
        
    except KeyboardInterrupt:
        if node:
            node.get_logger().info("⏹️ Interruzione da tastiera ricevuta")
    except Exception as e:
        if node:
            node.get_logger().error(f"❌ Errore nel nodo: {e}")
        else:
            print(f"❌ Errore main: {e}")
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
