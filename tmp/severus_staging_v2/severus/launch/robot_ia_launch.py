#!/usr/bin/env python3
# robot_ia_launch.py

"""
Launch file per la modalità 'Solo IA' di Marcus.
Include: AI Orchestrator, ReSpeaker (VUI + Hardware), Ultrasuoni, Foxglove e Home Assistant.
Esclude: Navigazione, Telecamere, SLAM e Controllo Motori.
"""

import os
import sys
from launch import LaunchDescription
from launch.actions import TimerAction, DeclareLaunchArgument, OpaqueFunction
from launch_ros.actions import Node, SetParameter
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

# Import Marcus Config Utils
pkg_share = get_package_share_directory('severus')
sys.path.append(os.path.join(pkg_share, 'utils'))
try:
    import config_utils
except ImportError:
    sys.path.append('/mnt/ssd/severus_host/severus/utils')
    import config_utils


def generate_launch_description():
    # FIX: CycloneDDS Participant Limit
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
        DeclareLaunchArgument('sim_mode', default_value='false', description='Enable simulation mode'),
    ]

    # URDF per visualizzazione in Foxglove
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

    # ReSpeaker VUI (Audio IA)
    respeaker_vui = Node(
        package='severus',
        executable='respeaker_vui_node',
        name='respeaker_vui_node',
        output='screen',
        parameters=[{
            # [v4.0] Sensitivity alzata a 0.92 per wake word custom .ppn in italiano.
            # Con custom keyword, Picovoice raccomanda 0.89-0.95.
            'wakeword_sensitivity': 0.92,
            # stt_gain: guadagno del segnale per il VAD (non per Porcupine)
            'stt_gain': 1.5,
        }]
    )

    # AI Orchestrator
    robot_ai_node = Node(
        package='severus',
        executable='robot_ai_node',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True,
        remappings=[
            ('/camera/image_raw', '/rgb/image'), # Rimarrà vuoto in questa configurazione
            ('/ai/input/text', '/robopy/conversation_rx'),
            ('/ask_visual_question', '/memory/visual_ask'),
            ('/memory_search', '/memory/search')
        ]
    )

    # Home Assistant Integration
    homeassistant_node = Node(
        package='severus',
        executable='homeassistant_node',
        name='homeassistant_node',
        output='screen',
        parameters=[{'update_interval': 150.0}]
    )

    # Tail Servo (Comunicazione non-verbale)
    servo_coda_node = Node(
        package='severus',
        executable='servo_coda_node',
        name='servo_coda_node',
        output='screen',
        parameters=[{'servo_pin': 18}]
    )

    # Ultrasonic Sensor (Distanza frontale)
    ultrasonic_node = Node(
        package='severus',
        executable='ultrasonic_sensor',
        name='ultrasonic_sensor',
        output='screen'
    )

    # ReSpeaker Interface (Hardware Control: LEDs, Wake Word, Audio Streaming)
    # NOTA: questo nodo gestisce TUTTA la comunicazione con il ReSpeaker Lite
    # via USB Serial JTAG (/dev/ttyACM0). Il VUI node è mantenuto come fallback
    # ma NON funziona senza una scheda audio ALSA (che il ReSpeaker Lite non ha).
    respeaker_node = Node(
        package='severus',
        executable='respeaker_interface_node',
        name='respeaker_interface_node',
        output='screen',
        parameters=[{
            'uart_port': '/dev/ttyACM0',
            'uart_baud': 921600,    # FIX: era 115200, deve corrispondere al firmware
            'enabled': True,
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
        
        # Nodi hardware (disabilitati in simulazione se necessario, qui semplificati per Marcus)
        respeaker_vui,
        respeaker_node,
        robot_ai_node,
        homeassistant_node,
        servo_coda_node,
        ultrasonic_node,
    ])
