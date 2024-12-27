import docker
import psutil
from typing import Dict, Any
from hypervisor_server import HypervisorServer
from models import VM, Resources
import logging
import gevent

logger = logging.getLogger(__name__)

class ProxmoxHypervisor(HypervisorServer):
    def create_vm(self, vm: VM) -> bool:
        logger.info("Proxmox: Creating VM %s (stub implementation)", vm.id)
        # Імітуємо тривалу операцію
        gevent.sleep(2)
        return True
        
    def delete_vm(self, vm: VM) -> bool:
        logger.info("Proxmox: Deleting VM %s (stub implementation)", vm.id)
        # Імітуємо тривалу операцію
        gevent.sleep(1)
        return True
        
    def get_resources(self) -> Dict[str, Any]:
        return {
            'cpu': 8,
            'ram': 16384,
            'storage': 1024000
        }

class VirtualBoxHypervisor(HypervisorServer):
    def create_vm(self, vm: VM) -> bool:
        logger.info("VirtualBox: Creating VM %s (stub implementation)", vm.id)
        # Імітуємо тривалу операцію
        gevent.sleep(2)
        return True
        
    def delete_vm(self, vm: VM) -> bool:
        logger.info("VirtualBox: Deleting VM %s (stub implementation)", vm.id)
        # Імітуємо тривалу операцію
        gevent.sleep(1)
        return True
        
    def get_resources(self) -> Dict[str, Any]:
        return {
            'cpu': 4,
            'ram': 8192,
            'storage': 512000
        }

class DockerHypervisor(HypervisorServer):
    def __init__(self):
        self.client = docker.from_env()
        super().__init__()
        
    def create_vm(self, vm: VM) -> bool:
        try:
            config = vm.config
            container = self.client.containers.run(
                image=config.get('image', 'ubuntu:latest'),
                name=f"{config.get('name')}-{vm.id}",
                detach=True,
                cpu_count=config.get('cpu', 1),
                mem_limit=f"{config.get('ram', 512)}m",
                volumes=config.get('volumes', {}),
                environment=config.get('environment', {}),
                network=config.get('network', 'bridge')
            )
            logger.info("Docker: Created container %s for VM %s", container.id, vm.id)
            return True
            
        except Exception as e:
            logger.error("Docker: Failed to create VM %s: %s", vm.id, str(e))
            return False
        
    def delete_vm(self, vm: VM) -> bool:
        try:
            container = self.client.containers.get(f"{vm.config.get('name')}-{vm.id}")
            container.remove(force=True)
            logger.info("Docker: Deleted container for VM %s", vm.id)
            return True
            
        except Exception as e:
            logger.error("Docker: Failed to delete VM %s: %s", vm.id, str(e))
            return False
        
    def get_resources(self) -> Dict[str, Any]:
        return {
            'cpu': psutil.cpu_count(),
            'ram': psutil.virtual_memory().total // (1024 * 1024),  # Convert to MB
            'storage': psutil.disk_usage('/').total // (1024 * 1024)  # Convert to MB
        }

HYPERVISOR_TYPES = {
    'proxmox': ProxmoxHypervisor,
    'virtualbox': VirtualBoxHypervisor,
    'docker': DockerHypervisor
}