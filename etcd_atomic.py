# etcd_atomic.py
import functools
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
import etcd3
from contextlib import ContextDecorator
from config import AppConfig
import gevent
import graphene
import uuid
import json

# for gevent compability
import grpc._cython.cygrpc
grpc._cython.cygrpc.init_grpc_gevent()


logger = logging.getLogger(__name__)

class EtcdClientManager:
    """Manages etcd client connection using configuration"""
    _instance = None
    _client = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_client(cls) -> etcd3.Etcd3Client:
        """Get etcd client instance with retry logic"""
        if cls._config is None:
            cls._config = AppConfig.load()
        
        if cls._client is None:
            cls._get_client()

        try:
            cls._client.status()
        except Exception:
            cls._get_client()
                    
        return cls._client

    @classmethod
    def _get_client(cls):
        _config = cls._config
        logger.info("Initializing etcd client connection to %s:%s", _config.etcd.host, _config.etcd.port)
        
        # Create client with retry logic
        retries = 0
        while retries < _config.etcd.max_retries:
            try:
                cls._client = etcd3.client(
                    host=_config.etcd.host,
                    port=_config.etcd.port,
                    timeout=_config.etcd.timeout
                )
                # Test connection
                cls._client.status()
                logger.info("Successfully connected to etcd")
                break
            except Exception as e:
                retries += 1
                logger.error("Failed to connect to etcd (attempt %d/%d): %s", 
                        retries, _config.etcd.max_retries, str(e))
                if retries == _config.etcd.max_retries:
                    logger.critical("Failed to connect to etcd after %d attempts", retries)
                    raise ConnectionError(f"Failed to connect to etcd after {retries} attempts: {str(e)}")
                gevent.sleep(_config.etcd.retry_interval)

class EtcdTransactionError(Exception):
    """Base exception for transaction-related errors"""
    pass

class EtcdTransaction:
    """Represents a single etcd transaction"""
    def __init__(self):
        self.client = EtcdClientManager.get_client()
        self.operations: List[Dict[str, Any]] = []
        self._committed = False
        self._rolled_back = False

    @property
    def is_active(self) -> bool:
        """Check if transaction is still active"""
        return not (self._committed or self._rolled_back)

    def add_operation(self, op_type: str, key: str, value: Any = None):
        """Add operation to transaction queue"""
        if not self.is_active:
            raise EtcdTransactionError("Transaction is no longer active")
        
        self.operations.append({
            'type': op_type,
            'key': key,
            'value': value
        })

    def commit(self) -> bool:
        """Commit all queued operations atomically"""
        if not self.is_active:
            logger.error("Attempting to commit inactive transaction")
            raise EtcdTransactionError("Transaction is no longer active")
        
        if not self.operations:
            logger.debug("Committing empty transaction")
            self._committed = True
            return True

        try:
            logger.debug("Starting transaction commit with %d operations", len(self.operations))
            # Build transaction operations
            txn_ops = []
            for op in self.operations:
                if op['type'] == 'put':
                    txn_ops.append(
                        self.client.transactions.put(op['key'], op['value'])
                    )
                elif op['type'] == 'delete':
                    txn_ops.append(
                        self.client.transactions.delete(op['key'])
                    )

            # Execute transaction
            success, response = self.client.transaction(
                compare=[],  # No comparisons for basic transaction
                success=txn_ops,
                failure=[]
            )

            if success:
                logger.info("Transaction committed successfully")
                self._committed = True
                return True
            else:
                logger.error("Transaction failed to commit")
                self.rollback()
                raise EtcdTransactionError("Transaction failed")

        except Exception as e:
            self.rollback()
            logger.error(f"Failed to commit transaction: {str(e)}")
            logger.info("Operations:\n" + "\n".join(f"  {vars(op)}" for op in txn_ops))
            raise EtcdTransactionError(f"Failed to commit transaction: {str(e)}")

    def rollback(self):
        """Rollback the transaction, discarding all operations"""
        self.operations.clear()
        self._rolled_back = True

class TransactionContext:
    """Manages transaction context for greenlets"""
    _local = {}

    @classmethod
    def get_current(cls) -> Optional[EtcdTransaction]:
        """Get current transaction for this greenlet"""
        greenlet_id = id(gevent.getcurrent())
        return cls._local.get(greenlet_id)

    @classmethod
    def set_current(cls, transaction: Optional[EtcdTransaction]):
        """Set current transaction for this greenlet"""
        greenlet_id = id(gevent.getcurrent())
        if transaction is None:
            cls._local.pop(greenlet_id, None)
        else:
            cls._local[greenlet_id] = transaction

def atomic(func=None):
    """
    Atomic transaction decorator and context manager
    
    Usage as decorator:
        @atomic
        def transfer_money(from_account, to_account, amount):
            from_account.balance -= amount
            from_account.save()
            to_account.balance += amount
            to_account.save()
            
    Usage as context manager:
        with atomic():
            model1.save()
            model2.save()
    """
    class AtomicContext:
        def __init__(self):
            self.transaction = None

        def __enter__(self) -> EtcdTransaction:
            current = TransactionContext.get_current()
            if current is not None:
                return current
            
            self.transaction = EtcdTransaction()
            TransactionContext.set_current(self.transaction)
            return self.transaction

        def __exit__(self, exc_type, exc_value, traceback):
            if self.transaction is not None:
                try:
                    if exc_type is None and self.transaction.is_active:
                        self.transaction.commit()
                    else:
                        self.transaction.rollback()
                finally:
                    TransactionContext.set_current(None)
            return False

    if func is None:
        return AtomicContext()
        
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with AtomicContext():
            return func(*args, **kwargs)
    return wrapper

class EtcdModelMixin:
    """Mixin to add etcd storage capabilities to models"""
    
    @property
    def etcd_client(self) -> etcd3.Etcd3Client:
        return EtcdClientManager.get_client()
    
    @classmethod
    def get_prefix(cls) -> str:
        """Get etcd key prefix for this model type"""
        return f"/vmgrid/{cls.__name__.lower()}/"
    
    def save(self) -> 'EtcdModelMixin':
        """Save model to etcd using nested keys"""
        # Update `updated_at` if the field exists
        if hasattr(self, 'updated_at'):
            self.updated_at = datetime.now().isoformat()
        
        base_key = f"{self.get_prefix()}{self.id}"
        current_txn = TransactionContext.get_current()
        operations = []

        # Delete existing list field entries first
        for field_name, field in self._meta.fields.items():
            if isinstance(field.type, graphene.List):
                list_prefix = f"{base_key}/{field_name}/"
                if current_txn:
                    # Add delete operation for the list prefix
                    current_txn.add_operation('delete', list_prefix)
                else:
                    # Delete all keys under this prefix
                    self.etcd_client.delete_prefix(list_prefix)

        # Save all fields
        for field_name, field in self._meta.fields.items():
            value = getattr(self, field_name)
            
            if isinstance(field.type, graphene.List):
                value = value if isinstance(value, list) else []
                for item in value:
                    list_key = f"{base_key}/{field_name}/{item}"
                    operations.append((list_key, str(item)))
            else:
                if value is not None:
                    key = f"{base_key}/{field_name}"
                    if field.type == graphene.JSONString:
                        str_value = json.dumps(value)
                    else:
                        str_value = str(value)
                    operations.append((key, str_value))
        
        if current_txn:
            for key, value in operations:
                current_txn.add_operation('put', key, value)
        else:
            for key, value in operations:
                self.etcd_client.put(key, value)
        
        return self

    
    @classmethod
    def to_graphene_types(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert string values to appropriate Graphene types"""
        fields = cls._meta.fields
        result = data.copy()
        
        for field_name, field in fields.items():
            if field_name not in result:
                continue
                
            value = result[field_name]
            if value is None:
                continue
            match field.type:
                case graphene.Int:
                    result[field_name] = int(value)
                case graphene.Float:
                    result[field_name] = float(value)
                case graphene.Boolean:
                    result[field_name] = value.lower() == 'true'
                case graphene.DateTime:
                    result[field_name] = datetime.fromisoformat(value)
                case graphene.JSONString:
                    try:
                        result[field_name] = json.loads(value) if isinstance(value, str) else value
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON for field {field_name}")
                        result[field_name] = value
                
        return result

    @classmethod
    def get(cls, id: str) -> Optional['EtcdModelMixin']:
        """
        Get model by ID from nested keys.
        """
       
        prefix = f"{cls.get_prefix()}{id}"
        client = EtcdClientManager.get_client()
        
        data = {}
        references = {}
        
        # Initialize list fields with empty lists
        for field_name, field in cls._meta.fields.items():
            if str(field.type) == str(graphene.List(graphene.String)):
                data[field_name] = list()
        
        for value, metadata in client.get_prefix(prefix):
            if not value:
                continue
                
            key_parts = metadata.key.decode('utf-8').split('/')
            if len(key_parts) == 5:  # Basic field
                field_name = key_parts[-1]
                data[field_name] = value.decode('utf-8')
            else:  # Nested reference
                ref_type = key_parts[-2]
                ref_id = value.decode('utf-8')
                if ref_type not in references:
                    references[ref_type] = []
                if ref_id not in references[ref_type]:
                    references[ref_type].append(ref_id)
        
        # Add references to data
        data.update(references)
                
        if data:
            data = cls.to_graphene_types(data)
            return cls(**data)
        return None
    
    @classmethod
    def delete(cls, id: str) -> Optional['EtcdModelMixin']:
        """
        Delete model by ID from nested keys.
        """

        prefix = f"{cls.get_prefix()}{id}"
        client = EtcdClientManager.get_client()
        return client.delete_prefix(prefix)

    @classmethod
    def all(cls) -> List['EtcdModelMixin']:
        """Get all models using nested keys"""
        prefix = cls.get_prefix()
        client = EtcdClientManager.get_client()
        
        models_data = {}
        
        for value, metadata in client.get_prefix(prefix):
            if not value:
                continue
                
            key_parts = metadata.key.decode('utf-8').split('/')
            # Extract ID from the correct position after prefix
            # /vmgrid/modelname/uuid/... -> uuid is at index 3
            if len(key_parts) <= 3:  # Skip malformed keys
                continue
                
            id_val = key_parts[3]  # UUID is always at position 3 after prefix split
            
            if id_val not in models_data:
                models_data[id_val] = {
                    field_name: list() if str(field.type) == str(graphene.List(graphene.String)) else None 
                    for field_name, field in cls._meta.fields.items()
                }
            
            # Handle basic field
            if len(key_parts) == 5:  # /vmgrid/modelname/uuid/fieldname
                field_name = key_parts[-1]
                models_data[id_val][field_name] = value.decode('utf-8')
            # Handle list field
            elif len(key_parts) == 6:  # /vmgrid/modelname/uuid/listfield/value
                field_name = key_parts[-2]
                ref_id = value.decode('utf-8')
                if isinstance(models_data[id_val][field_name], list) and ref_id not in models_data[id_val][field_name]:
                    models_data[id_val][field_name].append(ref_id)
        
        return [cls(**cls.to_graphene_types(data)) for data in models_data.values()]
    
    @classmethod
    def filter(cls, **kwargs) -> List['EtcdModelMixin']:
        """
        Filter models by field values
        
        Usage:
            Account.filter(username="john")
            Account.filter(balance__gt=100)
            Account.filter(username="john", balance__lt=1000)
        """
        def match_value(model_value: Any, filter_value: Any, operator: str = 'eq') -> bool:
            if operator == 'eq':
                return str(model_value).lower() == str(filter_value).lower()
            elif operator == 'gt':
                return float(model_value) > float(filter_value)
            elif operator == 'lt':
                return float(model_value) < float(filter_value)
            elif operator == 'gte':
                return float(model_value) >= float(filter_value)
            elif operator == 'lte':
                return float(model_value) <= float(filter_value)
            elif operator == 'contains':
                return str(filter_value).lower() in str(model_value).lower()
            elif operator == 'in':
                return str(model_value).lower() in [str(v).lower() for v in filter_value]
            return False

        # Get all objects
        all_objects = cls.all()
        filtered_objects = []

        # Process filter arguments
        filters = {}
        for key, value in kwargs.items():
            if '__' in key:
                field, operator = key.split('__')
            else:
                field, operator = key, 'eq'
            filters[field] = (operator, value)

        # Apply filters
        for obj in all_objects:
            matches = True
            for field, (operator, filter_value) in filters.items():
                model_value = getattr(obj, field, None)
                if model_value is None:
                    matches = False
                    break
                if not match_value(model_value, filter_value, operator):
                    matches = False
                    break
            if matches:
                filtered_objects.append(obj)

        return filtered_objects