import os
import base64
import json
from typing import Optional, Dict, Any, List
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from pathlib import Path
from functools import lru_cache

class SecretsManager:
    
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.encryption_key = self._get_encryption_key()
        self.fernet = Fernet(self.encryption_key)
        self._secrets_cache = {}
    
    def _get_encryption_key(self) -> bytes:
        master_key = os.getenv("MASTER_ENCRYPTION_KEY")
        
        if not master_key:
            if self.environment == "production":
                raise ValueError("MASTER_ENCRYPTION_KEY must be set in production")
            master_key = "dev-master-key-change-in-production"
        
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'seo-platform-salt',
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        return key
    
    def encrypt_secret(self, secret: str) -> str:
        encrypted = self.fernet.encrypt(secret.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def decrypt_secret(self, encrypted_secret: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(encrypted_secret.encode())
            decrypted = self.fernet.decrypt(decoded)
            return decrypted.decode()
        except Exception as e:
            raise ValueError(f"Failed to decrypt secret: {str(e)}")
    
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self._secrets_cache:
            return self._secrets_cache[key]
        
        value = os.getenv(key, default)
        
        if value and value.startswith("encrypted:"):
            encrypted_value = value.replace("encrypted:", "")
            value = self.decrypt_secret(encrypted_value)
        
        if value:
            self._secrets_cache[key] = value
        
        return value
    
    def set_secret(self, key: str, value: str, encrypt: bool = True):
        if encrypt:
            encrypted_value = self.encrypt_secret(value)
            os.environ[key] = f"encrypted:{encrypted_value}"
        else:
            os.environ[key] = value
        
        self._secrets_cache[key] = value
    
    def mask_secret(self, secret: str, visible_chars: int = 4) -> str:
        if not secret or len(secret) <= visible_chars:
            return "***"
        
        return secret[:visible_chars] + "***"
    
    def validate_required_secrets(self, required_keys: list) -> Dict[str, bool]:
        validation_results = {}
        
        for key in required_keys:
            value = self.get_secret(key)
            validation_results[key] = value is not None and len(value) > 0
        
        return validation_results
    
    def get_all_secrets(self, prefix: Optional[str] = None) -> Dict[str, str]:
        secrets = {}
        
        for key, value in os.environ.items():
            if prefix and not key.startswith(prefix):
                continue
            
            if any(sensitive in key.lower() for sensitive in ['key', 'token', 'secret', 'password']):
                secrets[key] = self.get_secret(key)
        
        return secrets
    
    def clear_cache(self):
        self._secrets_cache.clear()


class APICredentials:
    
    def __init__(self, secrets_manager: SecretsManager):
        self.sm = secrets_manager
    
    @property
    def openai_api_key(self) -> Optional[str]:
        return self.sm.get_secret("OPENAI_API_KEY")
    
    @property
    def gsc_credentials(self) -> Optional[Dict[str, Any]]:
        creds_json = self.sm.get_secret("GSC_CREDENTIALS")
        if creds_json:
            try:
                return json.loads(creds_json)
            except json.JSONDecodeError:
                return None
        return None
    
    @property
    def ga4_credentials(self) -> Optional[Dict[str, Any]]:
        creds_json = self.sm.get_secret("GA4_CREDENTIALS")
        if creds_json:
            try:
                return json.loads(creds_json)
            except json.JSONDecodeError:
                return None
        return None
    
    @property
    def yandex_webmaster_token(self) -> Optional[str]:
        return self.sm.get_secret("YANDEX_WEBMASTER_TOKEN")
    
    @property
    def pagespeed_api_key(self) -> Optional[str]:
        return self.sm.get_secret("PAGESPEED_API_KEY")
    
    @property
    def news_api_key(self) -> Optional[str]:
        return self.sm.get_secret("NEWS_API_KEY")
    
    @property
    def wordpress_credentials(self) -> Optional[Dict[str, str]]:
        site_url = self.sm.get_secret("WORDPRESS_SITE_URL")
        username = self.sm.get_secret("WORDPRESS_USERNAME")
        app_password = self.sm.get_secret("WORDPRESS_APP_PASSWORD")
        
        if site_url and username and app_password:
            return {
                "site_url": site_url,
                "username": username,
                "app_password": app_password
            }
        return None
    
    @property
    def tilda_api_key(self) -> Optional[str]:
        return self.sm.get_secret("TILDA_PUBLIC_KEY")
    
    @property
    def tilda_secret_key(self) -> Optional[str]:
        return self.sm.get_secret("TILDA_SECRET_KEY")
    
    def get_tilda_credentials(self) -> Optional[Dict[str, str]]:
        public_key = self.tilda_api_key
        secret_key = self.tilda_secret_key
        
        if public_key and secret_key:
            return {
                "public_key": public_key,
                "secret_key": secret_key
            }
        return None
    
    def get_database_url(self) -> str:
        full_url = self.sm.get_secret("DATABASE_URL")
        if full_url:
            return full_url
        
        host = self.sm.get_secret("POSTGRES_HOST", "postgres")
        port = self.sm.get_secret("POSTGRES_PORT", "5432")
        db = self.sm.get_secret("POSTGRES_DB", "seo_platform")
        user = self.sm.get_secret("POSTGRES_USER", "user")
        password = self.sm.get_secret("POSTGRES_PASSWORD", "password")
        
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    
    def get_redis_url(self) -> str:
        full_url = self.sm.get_secret("REDIS_URL")
        if full_url:
            return full_url
        
        host = self.sm.get_secret("REDIS_HOST", "redis")
        port = self.sm.get_secret("REDIS_PORT", "6379")
        db = self.sm.get_secret("REDIS_DB", "0")
        password = self.sm.get_secret("REDIS_PASSWORD")
        
        if password:
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"
    
    def get_rabbitmq_url(self) -> str:
        full_url = self.sm.get_secret("RABBITMQ_URL")
        if full_url:
            return full_url
        
        host = self.sm.get_secret("RABBITMQ_HOST", "rabbitmq")
        port = self.sm.get_secret("RABBITMQ_PORT", "5672")
        user = self.sm.get_secret("RABBITMQ_USER", "user")
        password = self.sm.get_secret("RABBITMQ_PASSWORD", "password")
        vhost = self.sm.get_secret("RABBITMQ_VHOST", "/")
        
        return f"amqp://{user}:{password}@{host}:{port}/{vhost}"
    
    @property
    def jwt_secret_key(self) -> str:
        return self.sm.get_secret(
            "JWT_SECRET_KEY",
            "change-this-secret-key-in-production"
        )
    
    @property
    def jwt_algorithm(self) -> str:
        return self.sm.get_secret("JWT_ALGORITHM", "HS256")
    
    def validate_token_permissions(self, token_data: Dict[str, Any], required_scopes: List[str]) -> bool:
        if not token_data:
            return False
        
        token_scopes = token_data.get('scopes', [])
        if isinstance(token_scopes, str):
            token_scopes = token_scopes.split()
        
        return all(scope in token_scopes for scope in required_scopes)
    
    def validate_gsc_token_scope(self, credentials: Dict[str, Any]) -> bool:
        required_scopes = [
            'https://www.googleapis.com/auth/webmasters.readonly'
        ]
        
        token_scopes = credentials.get('scopes', [])
        
        return all(scope in token_scopes for scope in required_scopes)
    
    def validate_ga4_token_scope(self, credentials: Dict[str, Any]) -> bool:
        required_scopes = [
            'https://www.googleapis.com/auth/analytics.readonly'
        ]
        
        token_scopes = credentials.get('scopes', [])
        
        return all(scope in token_scopes for scope in required_scopes)
    
    def validate_all(self) -> Dict[str, bool]:
        required_secrets = [
            "OPENAI_API_KEY",
            "JWT_SECRET_KEY",
        ]
        
        validation = self.sm.validate_required_secrets(required_secrets)
        
        validation["DATABASE"] = bool(self.get_database_url())
        validation["REDIS"] = bool(self.get_redis_url())
        validation["RABBITMQ"] = bool(self.get_rabbitmq_url())
        
        gsc_creds = self.gsc_credentials
        if gsc_creds:
            validation["GSC_SCOPE"] = self.validate_gsc_token_scope(gsc_creds)
        
        ga4_creds = self.ga4_credentials
        if ga4_creds:
            validation["GA4_SCOPE"] = self.validate_ga4_token_scope(ga4_creds)
        
        return validation


class AWSSecretsManager(SecretsManager):
    
    def __init__(self):
        super().__init__()
        self.region_name = os.getenv("AWS_REGION", "eu-central-1")
        self._boto3_client = None
    
    @property
    def client(self):
        if self._boto3_client is None:
            try:
                import boto3
                self._boto3_client = boto3.client(
                    'secretsmanager',
                    region_name=self.region_name
                )
            except ImportError:
                raise ImportError("boto3 is required for AWS Secrets Manager")
        return self._boto3_client
    
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self._secrets_cache:
            return self._secrets_cache[key]
        
        secret_name = f"seo-platform/{self.environment}/{key}"
        
        try:
            response = self.client.get_secret_value(SecretId=secret_name)
            
            if 'SecretString' in response:
                secret_value = response['SecretString']
                self._secrets_cache[key] = secret_value
                return secret_value
            else:
                decoded = base64.b64decode(response['SecretBinary'])
                self._secrets_cache[key] = decoded.decode()
                return decoded.decode()
        
        except self.client.exceptions.ResourceNotFoundException:
            return super().get_secret(key, default)
        except Exception:
            return super().get_secret(key, default)


class VaultSecretsManager(SecretsManager):
    
    def __init__(self):
        super().__init__()
        self.vault_addr = os.getenv("VAULT_ADDR", "http://vault:8200")
        self.vault_token = os.getenv("VAULT_TOKEN")
        self._hvac_client = None
    
    @property
    def client(self):
        if self._hvac_client is None:
            try:
                import hvac
                self._hvac_client = hvac.Client(
                    url=self.vault_addr,
                    token=self.vault_token
                )
            except ImportError:
                raise ImportError("hvac is required for Vault")
        return self._hvac_client
    
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self._secrets_cache:
            return self._secrets_cache[key]
        
        secret_path = f"seo-platform/{self.environment}/{key}"
        
        try:
            secret = self.client.secrets.kv.v2.read_secret_version(
                path=secret_path,
                mount_point='secret'
            )
            
            secret_value = secret['data']['data'].get('value')
            if secret_value:
                self._secrets_cache[key] = secret_value
                return secret_value
        
        except Exception:
            pass
        
        return super().get_secret(key, default)


@lru_cache()
def get_secrets_manager() -> SecretsManager:
    secrets_backend = os.getenv("SECRETS_BACKEND", "env")
    
    if secrets_backend == "aws":
        return AWSSecretsManager()
    elif secrets_backend == "vault":
        return VaultSecretsManager()
    else:
        return SecretsManager()


@lru_cache()
def get_api_credentials() -> APICredentials:
    sm = get_secrets_manager()
    return APICredentials(sm)


def load_secrets_from_file(filepath: str, secrets_manager: SecretsManager):
    path = Path(filepath)
    
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {filepath}")
    
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            
            if not line or line.startswith('#'):
                continue
            
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                
                secrets_manager.set_secret(key, value, encrypt=False)


def export_secrets_encrypted(output_file: str, secrets_manager: SecretsManager, keys: list):
    encrypted_secrets = {}
    
    for key in keys:
        value = secrets_manager.get_secret(key)
        if value:
            encrypted_secrets[key] = secrets_manager.encrypt_secret(value)
    
    with open(output_file, 'w') as f:
        json.dump(encrypted_secrets, f, indent=2)


def import_secrets_encrypted(input_file: str, secrets_manager: SecretsManager):
    with open(input_file, 'r') as f:
        encrypted_secrets = json.load(f)
    
    for key, encrypted_value in encrypted_secrets.items():
        decrypted_value = secrets_manager.decrypt_secret(encrypted_value)
        secrets_manager.set_secret(key, decrypted_value, encrypt=False)


class SecretRotation:
    
    def __init__(self, secrets_manager: SecretsManager):
        self.sm = secrets_manager
    
    def rotate_jwt_secret(self, new_secret: Optional[str] = None) -> str:
        if not new_secret:
            import secrets
            new_secret = secrets.token_urlsafe(64)
        
        old_secret = self.sm.get_secret("JWT_SECRET_KEY")
        
        if old_secret:
            self.sm.set_secret("JWT_SECRET_KEY_OLD", old_secret)
        
        self.sm.set_secret("JWT_SECRET_KEY", new_secret)
        
        return new_secret
    
    def rotate_api_key(self, key_name: str, new_value: str):
        old_value = self.sm.get_secret(key_name)
        
        if old_value:
            self.sm.set_secret(f"{key_name}_OLD", old_value)
        
        self.sm.set_secret(key_name, new_value)
    
    def rotate_database_password(self, new_password: str):
        self.sm.set_secret("POSTGRES_PASSWORD", new_password)
        self.sm.clear_cache()
    
    def rotate_redis_password(self, new_password: str):
        self.sm.set_secret("REDIS_PASSWORD", new_password)
        self.sm.clear_cache()


def validate_environment_secrets():
    sm = get_secrets_manager()
    credentials = get_api_credentials()
    
    validation = credentials.validate_all()
    
    missing_secrets = [key for key, valid in validation.items() if not valid]
    
    if missing_secrets:
        raise ValueError(
            f"Missing or invalid secrets: {', '.join(missing_secrets)}"
        )
    
    return True


def get_client_api_credentials(platform: str) -> Optional[Dict[str, Any]]:
    credentials = get_api_credentials()
    
    if platform.lower() == "wordpress":
        return credentials.wordpress_credentials
    elif platform.lower() == "tilda":
        return credentials.get_tilda_credentials()
    else:
        raise ValueError(f"Unsupported platform: {platform}")


if __name__ == '__main__':
    sm = get_secrets_manager()
    
    test_secret = "my-super-secret-api-key"
    encrypted = sm.encrypt_secret(test_secret)
    print(f"Encrypted: {encrypted}")
    
    decrypted = sm.decrypt_secret(encrypted)
    print(f"Decrypted: {decrypted}")
    
    masked = sm.mask_secret(test_secret)
    print(f"Masked: {masked}")
    
    credentials = get_api_credentials()
    print(f"\nOpenAI Key: {sm.mask_secret(credentials.openai_api_key or 'not-set')}")
    print(f"Database URL: {sm.mask_secret(credentials.get_database_url())}")
    print(f"Redis URL: {sm.mask_secret(credentials.get_redis_url())}")
    
    wp_creds = credentials.wordpress_credentials
    if wp_creds:
        print(f"WordPress: {wp_creds['site_url']}")
    
    tilda_creds = credentials.get_tilda_credentials()
    if tilda_creds:
        print(f"Tilda Key: {sm.mask_secret(tilda_creds['public_key'])}")
    
    validation = credentials.validate_all()
    print(f"\nValidation results:")
    for key, valid in validation.items():
        status = "✅" if valid else "❌"
        print(f"  {status} {key}")
