# models.py
import graphene
from etcd_atomic import EtcdModelMixin
from datetime import datetime
import uuid
from typing import Any, Dict, List, Optional


class VMStatus(graphene.Enum):
    PENDING = 'pending'
    CREATING = 'creating'
    DELETING = 'deleting'
    COMPLETED = 'completed'
    FAILED = 'failed'


class JobType(graphene.Enum):
    CREATE = 'create'
    DELETE = 'delete'


class VMProvisioningRequest(graphene.ObjectType, EtcdModelMixin):
    id = graphene.UUID()
    config = graphene.JSONString()
    created_by = graphene.String()
    name = graphene.String()
    vms = graphene.List(graphene.String)
    created_at = graphene.DateTime()
    updated_at = graphene.String()

    def __init__(self, **kwargs):
        kwargs.setdefault('id', str(uuid.uuid4()))
        kwargs.setdefault('created_at', datetime.now().isoformat())
        kwargs.setdefault('vms', [])
        super().__init__(**kwargs)


class VM(graphene.ObjectType, EtcdModelMixin):
    id = graphene.UUID()
    request_id = graphene.UUID()
    status = VMStatus()
    hypervisor_id = graphene.UUID()
    config = graphene.JSONString()
    created_at = graphene.DateTime()
    updated_at = graphene.String()

    def __init__(self, **kwargs):
        kwargs.setdefault('id', str(uuid.uuid4()))
        kwargs.setdefault('created_at', datetime.now().isoformat())
        super().__init__(**kwargs)


class Resources(graphene.ObjectType):
    cpu = graphene.Int()
    ram = graphene.Int()
    storage = graphene.Int()


class Hypervisor(graphene.ObjectType, EtcdModelMixin):
    id = graphene.UUID()
    type = graphene.String()
    available_resources = graphene.Field(Resources)
    vms = graphene.List(graphene.String)
    created_at = graphene.DateTime()
    updated_at = graphene.DateTime()

    def __init__(self, **kwargs):
        kwargs.setdefault('id', str(uuid.uuid4()))
        kwargs.setdefault('created_at', datetime.now().isoformat())
        kwargs.setdefault('vms', [])
        super().__init__(**kwargs)