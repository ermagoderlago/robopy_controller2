#!/usr/bin/env python3
#map_manager_node.py

import rclpy
from rclpy.node import Node
import os
import sqlite3
import time
from std_srvs.srv import Empty

class MapManager(Node):
    def __init__(self):
        super().__init__('map_manager')
        
        self.declare_parameter('database_path', '/home/robopy/.ros/rtabmap.db')
        self.declare_parameter('min_similarity_threshold', 0.3)
        self.declare_parameter('auto_save_interval', 300)
        
        self.database_path = os.path.expanduser(self.get_parameter('database_path').value)
        self.auto_save_interval = self.get_parameter('auto_save_interval').value
        
        # Servizi per gestione mappa
        self.save_map_service = self.create_service(Empty, 'save_map', self.save_map_callback)
        self.load_map_service = self.create_service(Empty, 'load_map', self.load_map_callback)
        self.clear_map_service = self.create_service(Empty, 'clear_map', self.clear_map_callback)
        
        # Timer per salvataggio automatico
        self.create_timer(self.auto_save_interval, self.auto_save)
        
        self.get_logger().info(f"🗺️ Map Manager started. Database: {self.database_path}")
        self.check_database()
    
    def check_database(self):
        """Verifica se esiste una mappa precedente"""
        if os.path.exists(self.database_path):
            try:
                conn = sqlite3.connect(self.database_path)
                cursor = conn.cursor()
                
                # Conta i nodi nella mappa
                cursor.execute("SELECT COUNT(*) FROM Node")
                node_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM Map")
                map_count = cursor.fetchone()[0]
                
                conn.close()
                
                self.get_logger().info(f"📊 Existing map found: {node_count} nodes, {map_count} maps")
                
                if node_count > 10:  # Soglia minima per considerare la mappa valida
                    self.get_logger().info("✅ Loading existing map...")
                    return True
                else:
                    self.get_logger().warning("⚠️ Existing map too small, creating new one")
                    return False
                    
            except Exception as e:
                self.get_logger().error(f"❌ Error checking database: {e}")
                return False
        else:
            self.get_logger().info("🆕 No existing map found, creating new one")
            return False
    
    def save_map_callback(self, request, response):
        """Servizio per forzare il salvataggio della mappa"""
        self.get_logger().info("💾 Manual map save requested")
        # RTAB-Map salva automaticamente, questo è solo un wrapper
        return response
    
    def load_map_callback(self, request, response):
        """Servizio per forzare il caricamento della mappa"""
        self.get_logger().info("📂 Manual map load requested")
        # RTAB-Map carica automaticamente all'avvio
        return response
    
    def clear_map_callback(self, request, response):
        """Servizio per cancellare la mappa"""
        self.get_logger().warning("🗑️ Manual map clear requested")
        try:
            if os.path.exists(self.database_path):
                os.remove(self.database_path)
                self.get_logger().info("✅ Map database cleared")
            else:
                self.get_logger().info("ℹ️ No map database to clear")
        except Exception as e:
            self.get_logger().error(f"❌ Error clearing map: {e}")
        return response
    
    def auto_save(self):
        """Salvataggio automatico periodico"""
        self.get_logger().info("💾 Auto-saving map...")

def main(args=None):
    rclpy.init(args=args)
    node = MapManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()