from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from models import VM, Hypervisor, Resources, VMProvisioningRequest
from task_queue import SharedJobQueue, HypervisorJobQueue, JobFailed
import os
import uuid
import logging
import gevent
from models import JobType, VMStatus
from etcd_atomic import atomic


class NotEnoughResources(Exception):
    """Raised when hypervisor doesn't have enough resources to create VM"""
    pass

logger = logging.getLogger(__name__)

class HypervisorServer(ABC):
    UUID_FILE = '/var/lib/vmgrid/uuid'
    
    def __init__(self):
        self.hypervisor = self._init_hypervisor()
        self._processing = False  # Flag to control processing
        logger.info(f"Hypervisor id: {self.hypervisor.id}")
        
    def _init_hypervisor(self) -> Hypervisor:
        """Initialize hypervisor instance from UUID file or create new"""
        os.makedirs(os.path.dirname(self.UUID_FILE), exist_ok=True)
        
        try:
            if os.path.exists(self.UUID_FILE):
                with open(self.UUID_FILE, 'r') as f:
                    hypervisor_id = f.read().strip()
                    if hypervisor_id:
                        hypervisor = Hypervisor.get(hypervisor_id)
                        if hypervisor:
                            return hypervisor
            
            # Create new hypervisor if file doesn't exist or is empty
            hypervisor = Hypervisor(
                type=self.__class__.__name__,
                available_resources=self.get_resources()
            ).save()
            
            # Save UUID to file
            with open(self.UUID_FILE, 'w') as f:
                f.write(str(hypervisor.id))
            
            return hypervisor
            
        except Exception as e:
            logger.error("Failed to initialize hypervisor: %s", str(e), exc_info=True)
            raise
    
    @abstractmethod
    def create_vm(self, vm: VM) -> bool:
        """Create a new VM"""
        pass
    
    @abstractmethod
    def delete_vm(self, vm: VM) -> bool:
        """Delete an existing VM"""
        pass
    
    @abstractmethod
    def get_resources(self) -> Dict[str, Any]:
        """Get available hypervisor resources"""
        pass

    def get_used_resources(self) -> Dict[str, int]:
        """Calculate total resources used by all VMs"""
        used_resources = {
            'cpu': 0,
            'ram': 0,
            'storage': 0
        }
        
        # Get all VMs assigned to this hypervisor
        if self.hypervisor.vms:
            for vm_id in self.hypervisor.vms:
                vm = VM.get(vm_id)
                if vm and vm.status != VMStatus.DELETING.value:
                    config = vm.config
                    used_resources['cpu'] += config.get('cpu', 0)
                    used_resources['ram'] += config.get('ram', 0)
                    used_resources['storage'] += config.get('storage', 0)
                
        return used_resources

    def get_free_resources(self) -> Dict[str, int]:
        """Calculate remaining free resources"""
        total = self.get_resources()
        used = self.get_used_resources()
        
        return {
            'cpu': total['cpu'] - used['cpu'],
            'ram': total['ram'] - used['ram'],
            'storage': total['storage'] - used['storage']
        }

    def can_accommodate_vm(self, vm: VM) -> bool:
        """Check if hypervisor has enough resources for the VM"""
        logger.info(f"Check resources for VM {vm.id}, {vm.config}")

        if not vm or not vm.config:
            return False
            
        free = self.get_free_resources()
        config = vm.config
        
        return (
            free['cpu'] >= config.get('cpu', 0) and
            free['ram'] >= config.get('ram', 0) and
            free['storage'] >= config.get('storage', 0)
        )
        
    def process_queue(self, queue):
        """Process tasks from shared queue"""
        logger.info(f"Start processing job queue {queue}")
        while self._processing:  # Check the processing flag
            try:
                with queue.pop(self.hypervisor.id) as job:
                    if job:
                        logger.info(f"Pop job {job.id}")
                        vm = VM.get(job.vm_id)
                        
                        if not vm:
                            logger.error("VM %s not found", job.vm_id)
                            continue

                        success = False
                        match job.job_type:
                            case JobType.CREATE.value:
                                logger.info(f"Creating VM: {vm.config}")
                                # Check resources before accepting create job
                                if not self.can_accommodate_vm(vm):
                                    logger.info("Insufficient resources for VM %s, raising exception", vm.id)
                                    raise NotEnoughResources(f"Not enough resources to create VM {vm.id}")
                                success = self.create_vm(vm)
                                if success:
                                    with atomic():
                                        vm.status = VMStatus.COMPLETED.value
                                        vm.hypervisor_id = self.hypervisor.id
                                        vm.save()
                                        self.hypervisor.vms.append(vm.id)
                                        self.hypervisor.save()
                            case JobType.DELETE.value:
                                logger.info(f"Deleting VM: {vm.id}")
                                # Delete tasks don't need resource check
                                success = self.delete_vm(vm)
                                if success:
                                    self.hypervisor.vms.remove(vm.id)
                                    self.hypervisor.save()
                                    vm.delete(vm.id)
                                    if not vm.filter(request_id=vm.request_id):
                                        request = VMProvisioningRequest.get(vm.request_id)
                                        request.delete(request.id)
                            case _:
                                logger.error("Unknown job type: %s", job.job_type)
                                success = False
                        if not success:
                            logger.error(f"Job id: {job.id} failed.")
                            raise JobFailed(f"Job id: {job.id} failed.")
            except Exception as e:
                logger.error(e, exc_info=True)
                                
            gevent.sleep(5)  # Prevent tight loop
            
    def start_processing(self):
        """Start processing both queues"""
        self._processing = True  # Set the processing flag to True
        gevent.joinall([
            gevent.spawn(self.process_queue, SharedJobQueue),
            gevent.spawn(self.process_queue, HypervisorJobQueue)
        ])

    def stop_processing(self):
        """Stop processing tasks from the queues"""
        self._processing = False  # Set the processing flag to False
        logger.info("Stopping hypervisor task processing...")