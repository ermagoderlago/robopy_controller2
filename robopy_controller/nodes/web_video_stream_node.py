#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import threading
import http.server
import socketserver
import io
import time

class WebVideoStream(Node):
    def __init__(self):
        super().__init__('web_video_stream')
        
        self.bridge = CvBridge()
        self.current_frame = None
        self.lock = threading.Lock()
        
        # Sottoscrizione al topic della camera
        self.subscription = self.create_subscription(
            Image,
            'image_raw',
            self.image_callback,
            10)
        
        # Avvia il server HTTP in un thread separato
        self.port = 8080
        self.server_thread = threading.Thread(target=self.start_web_server)
        self.server_thread.daemon = True
        self.server_thread.start()
        
        self.get_logger().info(f'Web video stream available at: http://{self.get_ip_address()}:{self.port}')
    
    def get_ip_address(self):
        """Ottiene l'indirizzo IP del Raspberry Pi"""
        import socket
        try:
            # Connette a un IP esterno per determinare l'interfaccia
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "localhost"
    
    def image_callback(self, msg):
        """Callback per ricevere i frame della camera"""
        try:
            # Converti il messaggio ROS2 in immagine OpenCV
            cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            
            with self.lock:
                self.current_frame = cv_image
                
        except Exception as e:
            self.get_logger().error(f'Error processing image: {str(e)}')
    
    def start_web_server(self):
        """Avvia il server HTTP per lo streaming video"""
        class VideoHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                self.node = kwargs.pop('node')
                super().__init__(*args, **kwargs)
            
            def do_GET(self):
                if self.path == '/video':
                    self.send_video_stream()
                else:
                    self.send_html_page()
            
            def send_html_page(self):
                """Invia una pagina HTML con il video stream"""
                html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Raspberry Pi Camera Stream</title>
                    <style>
                        body { margin: 0; padding: 20px; background: #333; color: white; }
                        h1 { text-align: center; }
                        #video-container { text-align: center; margin: 20px 0; }
                        img { max-width: 90%; border: 2px solid #555; }
                        .info { text-align: center; margin: 10px 0; }
                    </style>
                </head>
                <body>
                    <h1>Raspberry Pi Camera Live Stream</h1>
                    <div class="info">Streaming from ROS2 topic: /image_raw</div>
                    <div id="video-container">
                        <img id="video" src="/video" />
                    </div>
                    <div class="info" id="status">Connected</div>
                    <script>
                        function refreshVideo() {
                            var img = document.getElementById('video');
                            img.src = '/video?' + new Date().getTime();
                            document.getElementById('status').innerHTML = 'Last update: ' + new Date().toLocaleTimeString();
                        }
                        setInterval(refreshVideo, 100); // 10 FPS
                    </script>
                </body>
                </html>
                """
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(html.encode())
            
            def send_video_stream(self):
                """Invia il frame corrente come immagine JPEG"""
                try:
                    with self.node.lock:
                        frame = self.node.current_frame
                    
                    if frame is not None:
                        # Codifica il frame come JPEG
                        success, jpeg_data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        
                        if success:
                            self.send_response(200)
                            self.send_header('Content-type', 'image/jpeg')
                            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                            self.send_header('Pragma', 'no-cache')
                            self.send_header('Expires', '0')
                            self.end_headers()
                            self.wfile.write(jpeg_data.tobytes())
                            return
                    
                    # Se non c'è frame, invia un'immagine placeholder
                    self.send_response(404)
                    self.end_headers()
                    
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
        
        # Crea e avvia il server
        with socketserver.TCPServer(("", self.port), lambda *args, **kwargs: VideoHandler(*args, node=self, **kwargs)) as httpd:
            self.get_logger().info(f"Web server running on port {self.port}")
            httpd.serve_forever()

def main(args=None):
    rclpy.init(args=args)
    node = WebVideoStream()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()