#!/usr/bin/env python3
"""
Marcus Data Miner Node
======================
Wrapper di avvio ROS 2 per il nodo MarcusDataMiner con idle gating e persistenza su SSD.
"""

from robopy_controller.robot_ai.services.marcus_data_miner import main

if __name__ == '__main__':
    main()
