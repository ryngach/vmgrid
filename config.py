import os
import yaml
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ServerConfig:
    host: str
    port: int
    workers: int
    debug: bool

@dataclass
class EtcdConfig:
    host: str
    port: int
    timeout: int
    retry_interval: int
    max_retries: int

@dataclass
class GraphQLConfig:
    path: str
    graphiql: bool
    batch: bool
    max_depth: int

@dataclass
class LoggingConfig:
    level: str
    format: str

@dataclass
class HypervisorConfig:
    type: str

@dataclass
class AppConfig:
    server: ServerConfig
    etcd: EtcdConfig
    graphql: GraphQLConfig
    logging: LoggingConfig
    hypervisor: HypervisorConfig

    _instance = None
    _config_loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Skip initialization if already loaded
        if self._config_loaded:
            return
        self._load_config()

    def _load_config(self, env: str = None):
        if not env:
            env = os.getenv('APP_ENV', 'development')

        logger.info("Loading configuration for environment: %s", env)

        # Load default config
        with open('config/default.yaml', 'r') as f:
            config = yaml.safe_load(f)
            logger.debug("Loaded default config: %s", config)

        # Load environment specific config
        env_file = f'config/{env}.yaml'
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                env_config = yaml.safe_load(f)
                logger.debug("Loaded environment config: %s", env_config)
                self._deep_update(config, env_config)

        # Ensure hypervisor config exists with defaults
        if 'hypervisor' not in config:
            config['hypervisor'] = {}
        if 'type' not in config['hypervisor']:
            config['hypervisor']['type'] = 'docker'

        # Initialize instance attributes
        self.server = ServerConfig(**config['server'])
        self.etcd = EtcdConfig(**config['etcd'])
        self.graphql = GraphQLConfig(**config['graphql'])
        self.logging = LoggingConfig(**config['logging'])
        self.hypervisor = HypervisorConfig(**config['hypervisor'])

        self._config_loaded = True
        logger.info("Configuration loaded successfully")

    @staticmethod
    def _deep_update(target: Dict, source: Dict) -> Dict:
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                AppConfig._deep_update(target[key], value)
            else:
                target[key] = value
        return target

    @classmethod
    def get_instance(cls) -> 'AppConfig':
        """Get the singleton instance of AppConfig"""
        return cls()

    @classmethod
    def load(cls, env: str = None) -> 'AppConfig':
        """
        Maintain backwards compatibility with existing code.
        Now just returns the singleton instance.
        """
        instance = cls.get_instance()
        if env:
            # If environment is explicitly specified, reload the configuration
            instance._load_config(env)
        return instance