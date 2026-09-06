import rclpy
import time
import math
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from std_srvs.srv import Empty
from tf2_ros import Buffer, TransformListener

def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.degrees(math.atan2(siny_cosp, cosy_cosp))

def diff_deg(y1, y0):
    if y1 is None or y0 is None: return 0.0
    d = y1 - y0
    while d > 180: d -= 360
    while d < -180: d += 360
    return d

rclpy.init()
node = Node('rotation_verifier')

cmd_pub = node.create_publisher(Twist, '/cmd_vel', 10)
tf_buffer = Buffer()
tf_listener = TransformListener(tf_buffer, node)

wheel_odom = None
vio_odom = None
imu_integrated_yaw = 0.0
last_imu_time = None

def wheel_cb(msg):
    global wheel_odom
    wheel_odom = msg

def vio_cb(msg):
    global vio_odom
    vio_odom = msg

def imu_cb(msg):
    global imu_integrated_yaw, last_imu_time
    t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
    gz = msg.angular_velocity.z
    if last_imu_time is not None:
        dt = t - last_imu_time
        if 0 < dt < 0.2:
            imu_integrated_yaw += gz * dt
    last_imu_time = t

node.create_subscription(Odometry, '/odom_wheel', wheel_cb, 10)
node.create_subscription(Odometry, '/odom', vio_cb, 10)
node.create_subscription(Imu, '/oak/imu/data', imu_cb, 10)

def get_tf_yaw(source, target):
    try:
        t = tf_buffer.lookup_transform(source, target, rclpy.time.Time())
        return quat_to_yaw(t.transform.rotation)
    except Exception:
        return None

# Wake up sensors to ensure motion gate is open
wake_cli = node.create_client(Empty, '/robot/wake_sensors')
if wake_cli.wait_for_service(timeout_sec=2.0):
    wake_cli.call_async(Empty.Request())
    print("Called /robot/wake_sensors...")

# Wait 2.5s for LiDAR spin-up and gate opening
t0 = time.time()
while time.time() - t0 < 2.5:
    rclpy.spin_once(node, timeout_sec=0.05)

def run_micro_test(name, w_cmd, duration=0.35):
    global imu_integrated_yaw
    print(f"\n" + "="*65)
    print(f"🔄 {name}: w = {w_cmd:+.2f} rad/s per {duration:.2f}s")
    print("="*65)
    
    # Capture initial states
    imu_integrated_yaw = 0.0
    for _ in range(15):
        rclpy.spin_once(node, timeout_sec=0.03)
        
    wyaw0 = quat_to_yaw(wheel_odom.pose.pose.orientation) if wheel_odom else 0.0
    vyaw0 = quat_to_yaw(vio_odom.pose.pose.orientation) if vio_odom else 0.0
    tf_yaw0 = get_tf_yaw('odom', 'base_link')
    map_yaw0 = get_tf_yaw('map', 'base_link')
    imu_integrated_yaw = 0.0
    
    # Send command
    cmd = Twist()
    cmd.angular.z = float(w_cmd)
    t_end = time.time() + duration
    while time.time() < t_end:
        cmd_pub.publish(cmd)
        rclpy.spin_once(node, timeout_sec=0.02)
        
    # Stop command
    stop_cmd = Twist()
    for _ in range(5):
        cmd_pub.publish(stop_cmd)
        time.sleep(0.01)
        
    time.sleep(0.6) # Allow physical decel and message arrival
    for _ in range(15):
        rclpy.spin_once(node, timeout_sec=0.03)
        
    wyaw1 = quat_to_yaw(wheel_odom.pose.pose.orientation) if wheel_odom else 0.0
    vyaw1 = quat_to_yaw(vio_odom.pose.pose.orientation) if vio_odom else 0.0
    tf_yaw1 = get_tf_yaw('odom', 'base_link')
    map_yaw1 = get_tf_yaw('map', 'base_link')
    
    d_imu = math.degrees(imu_integrated_yaw)
    d_wheel = diff_deg(wyaw1, wyaw0)
    d_vio = diff_deg(vyaw1, vyaw0)
    d_tf = diff_deg(tf_yaw1, tf_yaw0)
    d_map = diff_deg(map_yaw1, map_yaw0)
    
    print(f"📊 RISULTATI DELTA ROTAZIONE:")
    print(f"   1. IMU Fisica Reale (/oak/imu/data):        {d_imu:+.2f}° ({'CCW / SX' if d_imu > 0 else 'CW / DX'})")
    print(f"   2. Odometria Ruote (/odom_wheel):          {d_wheel:+.2f}° ({'CCW / SX' if d_wheel > 0 else 'CW / DX'})")
    print(f"   3. FastFlow VIO (/odom):                   {d_vio:+.2f}° ({'CCW / SX' if d_vio > 0 else 'CW / DX'})")
    print(f"   4. TF odom -> base_link:                   {d_tf:+.2f}° ({'CCW / SX' if d_tf > 0 else 'CW / DX'})")
    print(f"   5. TF map -> base_link:                    {d_map:+.2f}° ({'CCW / SX' if d_map > 0 else 'CW / DX'})")
    
    expected_positive = (w_cmd > 0)
    wheel_ok = (d_wheel > 0) if expected_positive else (d_wheel < 0)
    print(f"\n   -> Odometria Ruote Verso: {'✅ CORRETTO CONCORDE' if wheel_ok else '❌ DISCORDE'}")
    return wheel_ok

# Test 1: Micro turn left (w = +0.55 rad/s, 0.35s)
res1 = run_micro_test("TEST 1: COMANDO SINISTRA (w = +0.55 rad/s)", w_cmd=0.55, duration=0.35)

time.sleep(1.0)

# Test 2: Micro turn right (w = -0.55 rad/s, 0.35s)
res2 = run_micro_test("TEST 2: COMANDO DESTRA (w = -0.55 rad/s, ritorno)", w_cmd=-0.55, duration=0.35)

print("\n" + "#"*65)
if res1 and res2:
    print("🏆 ESITO FINALE: POLARITÀ CINEMATICA E ODOMETRIA PERFETTAMENTE ALLINEATE!")
else:
    print("⚠️ ESITO FINALE: VERIFICA DISCREPANZE")
print("#"*65)

node.destroy_node()
rclpy.shutdown()
