# Re-export rosidl-generated messages
# This allows imports to work from source tree during development
try:
    from robopy_controller.msg._oak_sync_frame import OAKSyncFrame
    from robopy_controller.msg._keypoints_compressed import KeypointsCompressed
    from robopy_controller.msg._descriptors_compressed import DescriptorsCompressed
    from robopy_controller.msg._audio_data import AudioData
    from robopy_controller.msg._semantic_object import SemanticObject
    from robopy_controller.msg._semantic_object_array import SemanticObjectArray
    from robopy_controller.msg._engagement_status import EngagementStatus
    
    __all__ = [
        'OAKSyncFrame',
        'KeypointsCompressed',
        'DescriptorsCompressed',
        'AudioData',
        'SemanticObject',
        'SemanticObjectArray',
        'EngagementStatus',
    ]
except ImportError:
    # Messages not yet generated
    pass

