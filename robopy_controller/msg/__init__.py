# Re-export rosidl-generated messages
# This allows imports to work from source tree during development
try:
    from robopy_controller.msg._oak_sync_frame import OAKSyncFrame
    from robopy_controller.msg._keypoints_compressed import KeypointsCompressed
    from robopy_controller.msg._descriptors_compressed import DescriptorsCompressed
    
    __all__ = [
        'OAKSyncFrame',
        'KeypointsCompressed',
        'DescriptorsCompressed',
    ]
except ImportError:
    # Messages not yet generated
    pass
