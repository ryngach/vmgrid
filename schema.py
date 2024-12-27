import graphene
from models import Hypervisor, VM, VMProvisioningRequest, VMStatus, JobType
from task_queue import SharedJobQueue, HypervisorJobQueue
from etcd_atomic import atomic


class Resources(graphene.ObjectType):
    cpu = graphene.Int()
    ram = graphene.Int()
    storage = graphene.Int()


class VMConfigInput(graphene.InputObjectType):
    cpu = graphene.Int(required=True)
    ram = graphene.Int(required=True, description="Memoty size in MB")
    storage = graphene.Int(required=True)
    name = graphene.String(required=True)
    image = graphene.String(required=True)


class CreateVMsPayload(graphene.ObjectType):
    request_id = graphene.String()


class DeleteVMsPayload(graphene.ObjectType):
    success = graphene.Boolean()


class VMProvisioningRequestMeta(graphene.InputObjectType):
    name = graphene.String(required=True)
    created_by = graphene.String(required=True)


class Query(graphene.ObjectType):
    hypervisors = graphene.List(Hypervisor)
    hypervisor = graphene.Field(Hypervisor, id=graphene.UUID(required=True))
    vms = graphene.List(VM)
    vm = graphene.Field(VM, id=graphene.UUID(required=True))
    tasks = graphene.List(VMProvisioningRequest)
    task = graphene.Field(VMProvisioningRequest, id=graphene.UUID(required=True))
    shared_tasks = graphene.List(SharedJobQueue)
    shared_task = graphene.Field(SharedJobQueue, id=graphene.UUID(required=True))
    hypervisor_tasks = graphene.List(HypervisorJobQueue)
    hypervisor_task = graphene.Field(SharedJobQueue, id=graphene.UUID(required=True))

    def resolve_hypervisors(self, info):
        return Hypervisor.all()

    def resolve_hypervisor(self, info, id):
        return Hypervisor.get(id=id)

    def resolve_vms(self, info):
        return VM.all()

    def resolve_vm(self, info, id):
        return VM.get(id=id)

    def resolve_tasks(self, info):
        return VMProvisioningRequest.all()

    def resolve_task(self, info, id):
        return VMProvisioningRequest.get(id=id)
    
    def resolve_shared_tasks(self, info):
        return SharedJobQueue.all()

    def resolve_shared_task(self, info, id):
        return SharedJobQueue.get(id=id)
    
    def resolve_hypervisor_tasks(self, info):
        return HypervisorJobQueue.all()

    def resolve_hypervisor_task(self, info, id):
        return HypervisorJobQueue.get(hypervisor_id=id)


class CreateVMs(graphene.Mutation):
    class Arguments:
        count = graphene.Int(required=True)
        config = VMConfigInput(required=True)
        created_by = graphene.String(required=True)

    Output = CreateVMsPayload

    def mutate(self, info, count, config, created_by):
        request = VMProvisioningRequest(config = config, created_by = created_by)
        request.save()
        request.vms = []
        with atomic():
            for _ in range(count):
                vm = VM(request_id = request.id, config = config, status = VMStatus.PENDING.value)
                vm.save()
                request.vms.append(vm.id)
                SharedJobQueue.push(vm, job_type = JobType.CREATE.value)
            request.save()
        return CreateVMsPayload(request_id=request.id)


class DeleteVMs(graphene.Mutation):
    class Arguments:
        vm_ids = graphene.List(
            graphene.String,
            required=True
        )

    Output = DeleteVMsPayload

    def mutate(self, info, vm_ids):
        vms = VM.filter(id__in=vm_ids)
        for vm in vms:
            HypervisorJobQueue.push(vm, job_type=JobType.DELETE.value)
        
        return DeleteVMsPayload(success=True)


class Mutation(graphene.ObjectType):
    create_vms = CreateVMs.Field()
    delete_vms = DeleteVMs.Field()


schema = graphene.Schema(query=Query, mutation=Mutation)