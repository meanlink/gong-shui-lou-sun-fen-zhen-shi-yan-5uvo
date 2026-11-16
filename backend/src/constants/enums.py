from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class ShareStatus(str, Enum):
    building = 'building'
    ready = 'ready'
    expired = 'expired'
    revoked = 'revoked'
