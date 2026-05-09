from setuptools import setup, find_packages

package_name = 'severus'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(),
    zip_safe=True,
    maintainer='robopy',
    maintainer_email='robopy@todo.todo',
    description='Severus AI Package',
    license='MIT',
    entry_points={
        'console_scripts': [
            'robot_ai_node = severus.nodes.robot_ai_node:main',
            'respeaker_vui_node = severus.nodes.respeaker_vui_node:main',
            'respeaker_interface_node = severus.nodes.respeaker_interface_node:main',
            'homeassistant_node = severus.nodes.homeassistant_node:main',
            'servo_coda_node = severus.nodes.servo_coda_node:main',
            'ultrasonic_sensor = severus.nodes.ultrasonic_sensor:main',
        ],
    },
)
