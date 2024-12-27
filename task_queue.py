import graphene
import time
import gevent
from gevent.event import Event
from contextlib import contextmanager
from etcd_atomic import atomic, EtcdModelMixin, EtcdClientManager
import logging
from typing import Optional, Generator, List
from models import VM, VMStatus, JobType
import uuid
from etcd3.exceptions import PreconditionFailedError


logger = logging.getLogger(__name__)

class JobFailed(Exception):
    pass

class BaseJobQueue(graphene.ObjectType, EtcdModelMixin):
    """Base class for job queues with common functionality"""
    id: str = graphene.UUID()
    vm_id: str = graphene.UUID()
    request_id: str = graphene.UUID()
    hypervisor_id: str = graphene.UUID()
    job_type: str = JobType()
    timestamp: float = graphene.Float()

    @classmethod
    def get_pending_jobs(cls, hypervisor_id: str = None) -> List['BaseJobQueue']:
        """Get list of pending jobs - should be implemented by subclasses"""
        raise NotImplementedError

    @classmethod
    def handle_job_completion(cls, job: 'BaseJobQueue', success: bool) -> None:
        """Handle completion of hypervisor-specific jobs"""
        try:
            if job and job.hypervisor_id:
                if success:
                    cls.delete(job.id)
                logger.info("VM task %s completed with status %s", job.id,
                        VMStatus.COMPLETED.value if success else VMStatus.FAILED.value)
        except Exception as e:
            logger.error("Failed to complete task %s: %s", job.id, str(e))
            raise

    @classmethod
    @atomic
    def push(cls, vm: VM, job_type: JobType) -> 'BaseJobQueue':
        """Add job to queue"""
        logger.info("Pushing job %s to queue", vm.id)
        try:
            job_data = {
                "id": f"{vm.id}",
                "vm_id": vm.id,
                "request_id": vm.request_id,
                "job_type": job_type,
                "timestamp": time.time(),
                "hypervisor_id": vm.hypervisor_id if hasattr(vm, 'hypervisor_id') else None
            }
            job = cls(**job_data)
            return job.save()
        except Exception as e:
            logger.error("Failed to push job %s: %s", vm.id, str(e))
            raise

    @classmethod
    @contextmanager
    def pop(cls, hypervisor_id: str, task_timeout: int = 60) -> Generator[Optional['BaseJobQueue'], None, None]:
        """Context manager for processing tasks"""
        client = EtcdClientManager.get_client()
        lease = None
        acquired_task = None
        stop_event = Event()
        keep_alive_greenlet = None
        job_success = False

        def process_job(job):
            nonlocal lease, acquired_task, keep_alive_greenlet
            try:
                lease_id = uuid.UUID(job.id).int & 0xFFFFFFFF
                if client.get_lease_info(lease_id).TTL > 0:
                    logger.info("Lease already exists, skip job...")
                    return None

                lease = client.lease(ttl=task_timeout, lease_id=lease_id)
                logger.info(f"Lease created {lease.id}")
                
                success = client.transaction(
                    compare=[
                        client.transactions.value(f"{cls.get_prefix()}{job.id}/id") == job.id,
                        client.transactions.version(f"{cls.get_prefix()}{job.hypervisor_id}") == 0 
                    ],
                    success=[
                        client.transactions.put(
                            f"{cls.get_prefix()}{job.id}/hypervisor_id",
                            hypervisor_id,
                            lease=lease
                        ),
                        client.transactions.put(
                            f"/vms/{job.vm_id}/status",
                            VMStatus.CREATING.value if job.job_type == JobType.CREATE else VMStatus.DELETING.value,
                            lease=lease
                        ),
                        client.transactions.put(
                            f"/vms/{job.vm_id}/hypervisor_id",
                            hypervisor_id,
                            lease=lease
                        ),
                    ],
                    failure=[]
                )[0]

                if success:
                    logger.info("Job transaction success")
                    job.hypervisor_id = hypervisor_id
                    keep_alive_greenlet = cls._start_lease_keepalive(lease, stop_event, task_timeout)
                    logger.info("VM %s acquired by worker %s", job.vm_id, hypervisor_id)
                    acquired_task = job
                    return job
                else:
                    logger.info("Job transaction failed")
                    if lease:
                        lease.revoke()
                        lease = None
                    return None
                
            except PreconditionFailedError:
                if lease:
                    lease.revoke()
                    lease = None
                return

            except Exception as e:
                logger.error(f"Error processing job: {str(e)}", exc_info=True)
                if lease:
                    lease.revoke()
                    lease = None
                return None

        try:
            # Get and sort pending jobs
            pending_jobs = cls.get_pending_jobs(hypervisor_id)
            if not pending_jobs:
                yield None
                return

            pending_jobs.sort(key=lambda x: x.timestamp)
            
            # Try to process each job
            for job in pending_jobs:
                processed_job = process_job(job)
                if processed_job:
                    try:
                        yield processed_job
                    except:
                        job_success = False
                        raise
                    else:
                        job_success = True
                    finally:
                        return
            
            # If no job was successfully processed
            yield None
            
        finally:
            cls._cleanup(
                stop_event=stop_event,
                keep_alive_greenlet=keep_alive_greenlet,
                acquired_task=acquired_task,
                hypervisor_id=hypervisor_id,
                job_success=job_success,
                lease=lease
            )


    @staticmethod
    def _start_lease_keepalive(lease, stop_event: Event, task_timeout: int):
        """Start lease keepalive in background"""
        def keep_alive_worker():
            while not stop_event.is_set():
                try:
                    if lease.remaining_ttl > 0:
                        logger.info(f"Refresh lease {lease.id}")
                        lease.refresh()
                        gevent.sleep(task_timeout / 3)
                    else:
                        stop_event.set()
                        break
                except Exception as e:
                    logger.error("Error in lease keep-alive: %s", str(e))
                    stop_event.set()
                    break

        return gevent.spawn(keep_alive_worker)

    @classmethod
    def _cleanup(cls, stop_event: Event, keep_alive_greenlet, acquired_task,
                hypervisor_id: str, job_success: bool, lease):
        """Cleanup resources after job completion"""
        if stop_event:
            stop_event.set()
        
        if keep_alive_greenlet:
            gevent.joinall([keep_alive_greenlet], timeout=1)
        
        if acquired_task:
            try:
                cls.handle_job_completion(acquired_task, job_success)
            except Exception as e:
                logger.error("Error completing job: %s", str(e))
                if job_success:
                    try:
                        cls.handle_job_completion(acquired_task, False)
                    except Exception:
                        logger.error("Failed to mark job as failed")
        
        if lease:
            try:
                lease.revoke()
            except Exception as e:
                logger.error("Error revoking lease: %s", str(e))


class SharedJobQueue(BaseJobQueue):
    """Queue for jobs that can be processed by any hypervisor"""
    
    @classmethod
    def get_pending_jobs(cls, hypervisor_id: str = None) -> List[BaseJobQueue]:
        return [t for t in cls.all() if not t.hypervisor_id]


class HypervisorJobQueue(BaseJobQueue):
    """Queue for jobs that must be processed by a specific hypervisor"""
    
    @classmethod
    def get_pending_jobs(cls, hypervisor_id: str = None) -> List[BaseJobQueue]:
        return cls.filter(hypervisor_id=hypervisor_id)