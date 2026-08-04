#!/usr/bin/env python3
# robot_ia_launch.py

"""
Launch file per la modalità 'Solo IA' di Marcus. [v5.8]
Include: AI Orchestrator, ReSpeaker (VUI + Hardware), Ultrasuoni, Foxglove e Home Assistant.
Unifica i miglioramenti locali (Barge-In, Volume 1%) con i fix del Pi (CycloneDDS, config_utils).
[v12.0] Aggiunto supporto per enable_adaptive_threshold e enable_adaptive_silence
        per calibrazione automatica del microfono e silenzio VAD adattivo.
"""

import os
import sys
from launch import LaunchDescription
from launch.actions import TimerAction, DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, SetParameter
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

# Import Marcus Config Utils (Path specifico del Pi)
pkg_share = get_package_share_directory('robopy_controller')
sys.path.append(os.path.join(pkg_share, 'utils'))
try:
    import config_utils
except ImportError:
    # Fallback per sviluppo locale se necessario
    sys.path.append('/mnt/ssd/robopy_controller_host/robopy_controller/utils')
    try:
        import config_utils
    except ImportError:
        pass


def generate_launch_description():
    # FIX: CycloneDDS Participant Limit (Mantieni logic del Pi)
    dds_config = "/tmp/cyclonedds_robopy.xml"
    if not os.path.exists(dds_config):
        with open(dds_config, "w") as f:
            f.write('''<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
    <Domain id="any">
        <Discovery>
            <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
        </Discovery>
    </Domain>
</CycloneDDS>''')
    os.environ["CYCLONEDDS_URI"] = f"file://{dds_config}"

    # Launch Arguments
    args = [
        DeclareLaunchArgument('sim_mode',     default_value='false', description='Enable simulation mode'),
        DeclareLaunchArgument('audio_volume', default_value='0.05',  description='AI Audio software gain (0.0 to 1.0)'),
    ]

    # URDF per visualizzazione in Foxglove
    # Prova a caricare marcus.urdf o robopy.urdf in base a disponibilità
    urdf_file = os.path.join(pkg_share, 'urdf', 'marcus.urdf')
    if not os.path.exists(urdf_file):
        urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
        
    with open(urdf_file, 'r') as f:
        robot_description = ParameterValue(f.read(), value_type=str)

    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_description}],
        output='screen'
    )

    # Static TF per sensore ultrasuoni
    ultrasonic_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_ultrasonic_tf',
        arguments=[
            '0.12', '0.0', '0.05',
            '0.0', '0.0', '0.0',
            'base_link',
            'ultrasonic_sensor'
        ],
        output='log'
    )

    # Foxglove Bridge (Telemetria)
    foxglove = Node(
        package='foxglove_bridge',
        executable='foxglove_bridge',
        name='foxglove_bridge',
        output='log',
        parameters=[{'port': 8765}]
    )

    # ReSpeaker VUI (Audio IA) — [v12.0] Auto-calibrazione microfono + silenzio adattivo
    respeaker_vui = Node(
        package='robopy_controller',
        executable='respeaker_vui_node',
        name='respeaker_vui_node',
        output='screen',
        parameters=[{
            'wakeword_sensitivity': 0.92,
            # [v18.0] Guadagno 2.5x tarato con calibrazione empirica ed AGC
            'stt_gain': 2.5,
            # [v18.0] Threshold iniziale calibrata su segnale HPF
            'noise_gate_threshold': 400.0,
            # --- Barge-In (v5.7) ---
            'enable_barge_in': True,
            'barge_in_min_tts_ms': 2500.0,
            'barge_in_min_frames': 10,  # [v19.0] Armonizzato a 200ms
            # --- [v17.0] Auto-calibrazione microfono far-field HPF ---
            # Calcola dinamicamente noise_gate_threshold su rumore HPF (ventola Pi 5 eliminata)
            # Formula: clamp(EMA_HPF * stt_gain * 1.25 + 300, 800.0, 4500.0)
            'enable_adaptive_threshold': True,
            # --- [v17.0] Silenzio VAD adattivo ---
            'enable_adaptive_silence': True,
            # Diagnostica estesa
            'diag_mode': True,
        }]
    )

    # AI Orchestrator
    robot_ai_node = Node(
        package='robopy_controller',
        executable='robot_ai_node',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True,
        parameters=[{
            'gemini_api_key': os.getenv('GEMINI_API_KEY', ''),
            'audio_volume': LaunchConfiguration('audio_volume'),
        }],
        remappings=[
            ('/ai/input/text', '/robopy/conversation_rx'),
        ]
    )

    # Home Assistant Integration
    homeassistant_node = Node(
        package='robopy_controller',
        executable='homeassistant_node',
        name='homeassistant_node',
        output='screen',
        parameters=[{
            'ha_token': os.getenv('HA_TOKEN', ''),
            'update_interval': 150.0
        }]
    )

    # Tail Servo
    servo_coda_node = Node(
        package='robopy_controller',
        executable='servo_coda_node',
        name='servo_coda_node',
        output='screen',
        parameters=[{'servo_pin': 18}]
    )

    # Ultrasonic Sensor
    ultrasonic_node = Node(
        package='robopy_controller',
        executable='ultrasonic_sensor',
        name='ultrasonic_sensor',
        output='screen'
    )

    # ReSpeaker Interface (Hardware)
    respeaker_node = Node(
        package='robopy_controller',
        executable='respeaker_interface_node',
        name='respeaker_interface_node',
        output='screen',
        parameters=[{
            'uart_port': '/dev/ttyACM0',
            'uart_baud': 921600,
            'enabled': True,
            'default_volume': 5,    # Volume al 5% (v6.6)
            'enable_aec': True,
            'enable_agc': False,
            'enable_ns':  True,
        }]
    )

    # Prepara l'ambiente per GStreamer (necessario per audio su Pi5)
    if 'GST_PLUGIN_PATH' not in os.environ:
        os.environ['GST_PLUGIN_PATH'] = '/usr/local/lib/gstreamer-1.0:/usr/local/lib/aarch64-linux-gnu/gstreamer-1.0:'

    return LaunchDescription([
        *args,
        SetParameter('use_sim_time', os.environ.get('SIM_MODE', 'false').lower() == 'true'),

        robot_state_publisher,
        ultrasonic_tf,
        foxglove,
        
        # Nodi hardware
        respeaker_vui,
        respeaker_node,
        robot_ai_node,
        homeassistant_node,
        servo_coda_node,
        ultrasonic_node,
    ])
