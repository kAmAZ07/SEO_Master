"""Own disposable WordPress/MariaDB fixture; no shared stack or production deploy."""
from contextlib import AbstractContextManager
import json
import io
from pathlib import Path
import secrets
import subprocess
import tarfile
import time
from typing import Any
import uuid
from scripts.project import build_environment

WORDPRESS = "wordpress:6.8.3-php8.3-apache@sha256:30bff39330d1693b0ce13d32fc9b7bb67193064f040b7d60d3494e136fa599d4"
MARIADB = "mariadb:10.11@sha256:d769e37960821dfdcf096954f6e7ac89f747e116c1bba98e48edd265dfc7deae"
POSTGRES = "postgres:15-alpine@sha256:09e4f20b14ddb3dfe3a0c825b206032aaf8f28300ba2070c0b60fc1c10c6abc7"


class NativeWordPress(AbstractContextManager["NativeWordPress"]):
    def __init__(self, root: Path, *, project_id: str, site_id: str, context: str = "desktop-linux"):
        self.root, self.project_id, self.site_id = root.resolve(strict=True), project_id, site_id
        self.docker = ["docker", "--context", context]
        self.tool_environment = build_environment()
        self.owner = "seo-wp-contract-" + uuid.uuid4().hex
        self.network = self.owner + "-net"
        self.db, self.wp = self.owner + "-db", self.owner + "-wp"
        self.secret, self.receipt_secret = secrets.token_hex(32), secrets.token_hex(32)
        self.private_values = [self.secret, self.receipt_secret]
        self.resources: list[tuple[str, str]] = []
        self.port = 0
        self.entity_id = 0
        self.postgres_url: str | None = None

    def run(self, *args: str, stdin: str | None = None, timeout: int = 90) -> str:
        result = subprocess.run([*self.docker, *args], input=stdin, env=self.tool_environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if result.returncode:
            # argv/env may contain randomly generated fixture credentials: never dump them.
            diagnostic = result.stderr + result.stdout if args[0] == "exec" and "php" in args else ""
            for value in self.private_values:
                diagnostic = diagnostic.replace(value, "[fixture-secret]")
            raise RuntimeError(f"native WordPress Docker {args[0]} failed (exit {result.returncode}): {diagnostic[-2500:]}")
        return result.stdout

    def php(self, source: str, *, installing: bool = False) -> str:
        prelude = "define('WP_DISABLE_FATAL_ERROR_HANDLER', true); $_SERVER['HTTP_HOST']='wordpress.test'; $_SERVER['REQUEST_URI']='/';\n"
        if installing:
            prelude += "define('WP_INSTALLING', true);\n"
        return self.run("exec", "-i", self.wp, "php", stdin="<?php\n" + prelude + "require '/var/www/html/wp-load.php';\n" + source)

    def copy_plugin(self) -> None:
        # docker cp does not reliably write into user tmpfs mounts. Extract via the
        # running container, keeping all writes inside its disposable filesystem.
        payload = io.BytesIO()
        plugin = self.root / "adapters/wordpress-plugin"
        with tarfile.open(fileobj=payload, mode="w") as archive:
            for path in sorted(plugin.rglob("*.php")):
                if path.is_symlink() or not path.resolve().is_relative_to(plugin):
                    raise RuntimeError("native fixture refuses linked plugin sources")
                archive.add(path, arcname=str(path.relative_to(plugin)).replace("\\", "/"), recursive=False)
        result = subprocess.run([*self.docker, "exec", "-i", self.wp, "tar", "-xf", "-", "-C",
                                 "/var/www/html/wp-content/plugins/seo-master"], input=payload.getvalue(),
                                env=self.tool_environment, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError("native fixture source copy failed")

    def start_postgres(self) -> str:
        if self.postgres_url is not None:
            return self.postgres_url
        name = self.owner + "-pg"
        password = secrets.token_hex(32)
        self.private_values.append(password)
        self.run("run", "-d", "--rm", "--name", name, "--network", self.network,
                 "--label", "seo.test.owner=" + self.owner, "--network-alias", "postgres", "--publish", "127.0.0.1::5432",
                 "--tmpfs", "/var/lib/postgresql/data:rw,nosuid", "-e", "POSTGRES_PASSWORD=" + password, POSTGRES)
        self.resources.append(("container", name))
        for _ in range(30):
            try:
                self.run("exec", name, "pg_isready", "-U", "postgres", timeout=5)
                break
            except RuntimeError:
                time.sleep(1)
        else:
            raise RuntimeError("isolated PostgreSQL readiness timed out")
        info = json.loads(self.run("inspect", name))[0]
        port = int(info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
        self.postgres_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        return self.postgres_url

    def __enter__(self) -> "NativeWordPress":
        try:
            self.run("network", "create", "--label", "seo.test.owner=" + self.owner, self.network)
            self.resources.append(("network", self.network))
            password = secrets.token_hex(32)
            self.private_values.append(password)
            self.run("run", "-d", "--rm", "--name", self.db, "--network", self.network, "--network-alias", "db",
                     "--label", "seo.test.owner=" + self.owner, "--tmpfs", "/var/lib/mysql:rw,nosuid",
                     "-e", "MARIADB_ROOT_PASSWORD=" + password, "-e", "MARIADB_DATABASE=wordpress", MARIADB)
            self.resources.append(("container", self.db))
            for _ in range(60):
                try:
                    self.run("exec", self.db, "healthcheck.sh", "--connect", "--innodb_initialized", timeout=5)
                    break
                except RuntimeError:
                    time.sleep(1)
            else:
                raise RuntimeError("isolated MariaDB readiness timed out")
            self.run("run", "-d", "--rm", "--name", self.wp, "--network", self.network,
                     "--network-alias", "wordpress.test",
                     "--label", "seo.test.owner=" + self.owner, "--publish", "127.0.0.1::80",
                     "--tmpfs", "/var/www/html:rw,nosuid", "-e", "WORDPRESS_DB_HOST=db", "-e", "WORDPRESS_DB_USER=root",
                     "-e", "WORDPRESS_DB_PASSWORD=" + password, "-e", "WORDPRESS_DB_NAME=wordpress",
                     "-e", "WORDPRESS_DEBUG=1",
                     "-e", "WORDPRESS_CONFIG_EXTRA=define('DISABLE_WP_CRON', true);",
                     "-e", "SEO_MASTER_KEY_ID=fixture", "-e", "SEO_MASTER_HMAC_SECRET=" + self.secret,
                     "-e", "SEO_MASTER_RECEIPT_SECRET=" + self.receipt_secret, "-e", "SEO_MASTER_TRUSTED_GATEWAY=1", WORDPRESS)
            self.resources.append(("container", self.wp))
            for _ in range(30):
                try:
                    self.run("exec", self.wp, "test", "-f", "/var/www/html/wp-config.php", timeout=5)
                    break
                except RuntimeError:
                    time.sleep(1)
            else:
                raise RuntimeError("isolated WordPress setup timed out")
            info = json.loads(self.run("inspect", self.wp))[0]
            self.port = int(info["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"])
            self.run("exec", self.wp, "mkdir", "-p", "/var/www/html/wp-content/plugins/seo-master")
            self.copy_plugin()
            self.run("exec", self.wp, "test", "-f", "/var/www/html/wp-content/plugins/seo-master/seo-master-plugin.php")
            admin_password = secrets.token_hex(24)
            self.private_values.append(admin_password)
            # Only this newly created, labelled, tmpfs installation receives installation DDL.
            result = self.php(r"""
require_once ABSPATH . 'wp-admin/includes/upgrade.php';
add_filter('pre_wp_mail', static fn() => true);
wp_install('AR4 Native Fixture', 'fixture_admin', 'fixture@example.test', false, '', %s);
update_option('home', 'http://wordpress.test');
update_option('siteurl', 'http://wordpress.test');
update_option('permalink_structure', '/%%postname%%');
update_option('seo_master_project_id', %s);
update_option('seo_master_site_id', %s);
update_option('active_plugins', ['seo-master/seo-master-plugin.php']);
require_once WP_PLUGIN_DIR . '/seo-master/seo-master-plugin.php';
global $wpdb;
// The plugin explicitly requires strict SQL; vanilla wpdb removes it by default.
// Configure only this fixture's sessions; the runtime guard stays unchanged.
$wpdb->query("SET SESSION sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'");
if (!is_dir(WPMU_PLUGIN_DIR)) { mkdir(WPMU_PLUGIN_DIR, 0755, true); }
file_put_contents(WPMU_PLUGIN_DIR.'/fixture-strict.php', '<?php global $wpdb; $wpdb->query("SET SESSION sql_mode=\'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION\'");');
seo_master_v2_store()->install_schema();
$id = wp_insert_post(['post_type'=>'post','post_status'=>'publish','post_name'=>'catalog','post_title'=>'Old title']);
update_post_meta($id, '_seo_master_meta_title', 'Мастерская обуви');
flush_rewrite_rules(true);
echo json_encode(['entity_id'=>$id]);
""" % (json.dumps(admin_password), json.dumps(self.project_id), json.dumps(self.site_id)), installing=True)
            self.entity_id = int(json.loads(result)["entity_id"])
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args: Any) -> None:
        for kind, name in reversed(self.resources):
            info = json.loads(self.run(kind if kind == "network" else "container", "inspect", name))[0]
            labels = info.get("Labels", {}) if kind == "network" else info["Config"].get("Labels", {})
            if labels.get("seo.test.owner") != self.owner or not name.startswith(self.owner + "-"):
                raise RuntimeError("refusing native fixture cleanup: ownership mismatch")
            self.run("network", "rm", name) if kind == "network" else self.run("stop", name)
            if kind == "container" and not info["HostConfig"]["AutoRemove"]:
                self.run("container", "rm", name)
        self.resources.clear()
