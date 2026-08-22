from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'robot_simulation'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'urdf'), glob('urdf/*.xacro')),
        (os.path.join('share', package_name, 'worlds'), glob('worlds/*.sdf')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Marcus Team',
    maintainer_email='marcus@example.com',
    description='Ambiente di simulazione per Marcus su Gazebo Harmonic in WSL2',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'yolo_cpu_mock_node = robot_simulation.yolo_cpu_mock_node:main',
            'vui_mock_node = robot_simulation.vui_mock_node:main',
            'synthetic_robot_sim_node = robot_simulation.synthetic_robot_sim_node:main',
        ],
    },
)
