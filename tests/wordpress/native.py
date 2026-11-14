"""Own disposable WordPress/MariaDB fixture; no shared stack or production deploy."""
from contextlib import AbstractContextManager
import json
import base64
from pathlib import Path
import secrets
import subprocess
import time
from typing import Any
import uuid
from scripts.project import build_environment
from scripts.plugin_package import committed_payload
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from shared.plugin_package import PAYLOAD, PackageError, build_package, public_key_bytes, sha256, verify_package, zip_bytes

WORDPRESS = "wordpress:6.8.3-php8.3-apache@sha256:30bff39330d1693b0ce13d32fc9b7bb67193064f040b7d60d3494e136fa599d4"
MARIADB = "mariadb:10.11@sha256:d769e37960821dfdcf096954f6e7ac89f747e116c1bba98e48edd265dfc7deae"
POSTGRES = "postgres:15-alpine@sha256:09e4f20b14ddb3dfe3a0c825b206032aaf8f28300ba2070c0b60fc1c10c6abc7"


class NativeWordPress(AbstractContextManager["NativeWordPress"]):
    def __init__(self, root: Path, *, project_id: str, site_id: str, context: str = "desktop-linux", revision: str | None = None):
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
        # Test-only provenance (all-zero commit), never a published release or
        # a claim that dirty working bytes correspond to HEAD.
        key = Ed25519PrivateKey.generate()
        public = public_key_bytes(key)
        self.release_keys = {sha256(public): public}
        payload = {}
        plugin = self.root / 'adapters/wordpress-plugin'
        self.source_commit = '0' * 40
        if revision is None:
            for name in PAYLOAD:
                path = plugin / name
                if path.is_symlink() or not path.resolve(strict=True).is_relative_to(plugin):
                    raise RuntimeError('native fixture refuses linked plugin source')
                payload[name] = path.read_bytes().replace(b'\r\n', b'\n')
        else:
            self.source_commit, payload = committed_payload(self.root, revision)
        self.release_package = build_package(payload, self.source_commit, key)
        old_payload = {**payload, 'readme.txt': payload['readme.txt'] + b'\nDisposable installer baseline fixture.\n'}
        self.baseline_package = build_package(old_payload, '0' * 40, key)

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

    def copy_plugin(self, package: bytes) -> None:
        # External verification precedes any extraction/PHP execution. Transport
        # carries the exact verified bytes, not a reopened source path.
        verify_package(package, self.release_keys, expected_sha256=sha256(package))
        self.transport_plugin(package)

    def transport_plugin(self, package: bytes) -> None:
        # Test transport only. The sole unchecked caller is the negative
        # upgrader-hook test, explicitly exercising defence in depth.
        source = "<?php $data=base64_decode('" + base64.b64encode(package).decode('ascii') + "', true);"
        source += "if (hash('sha256',$data)!=='" + sha256(package) + "') { exit(2); }"
        source += "if (file_put_contents('/tmp/seo-package.zip',$data)!==strlen($data)) { exit(3); }"
        self.run('exec', '-i', self.wp, 'php', stdin=source)

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
                     "-e", "SEO_MASTER_RELEASE_KEYS_JSON=" + json.dumps({identity: base64.b64encode(value).decode('ascii') for identity, value in self.release_keys.items()}),
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
            self.copy_plugin(self.baseline_package)
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
require_once ABSPATH . 'wp-admin/includes/file.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
if (!defined('FS_METHOD')) { define('FS_METHOD', 'direct'); }
ob_start();
$upgrader = new Plugin_Upgrader(new Automatic_Upgrader_Skin());
$installed = $upgrader->install('/tmp/seo-package.zip', ['clear_update_cache' => false]);
ob_end_clean();
if ($installed !== true) { throw new RuntimeException('signed_zip_install_failed'); }
wp_set_current_user(get_user_by('login', 'fixture_admin')->ID);
$activated = activate_plugin('seo-master-connector/seo-master-plugin.php');
if (is_wp_error($activated) || !is_plugin_active('seo-master-connector/seo-master-plugin.php')) { throw new RuntimeException('signed_zip_activation_failed'); }
if (!seo_master_verify_current_plugin_signature()) { throw new RuntimeException('installed_release_integrity_failed'); }
global $wpdb;
// The plugin explicitly requires strict SQL; vanilla wpdb removes it by default.
// Configure only this fixture's sessions; the runtime guard stays unchanged.
$wpdb->query("SET SESSION sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'");
if (!is_dir(WPMU_PLUGIN_DIR)) { mkdir(WPMU_PLUGIN_DIR, 0755, true); }
file_put_contents(WPMU_PLUGIN_DIR.'/fixture-strict.php', '<?php global $wpdb; $wpdb->query("SET SESSION sql_mode=\'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION\'");');
seo_master_v2_store()->install_schema();
$id = wp_insert_post(['post_type'=>'post','post_status'=>'publish','post_name'=>'catalog','post_title'=>'Old title']);
update_post_meta($id, '_seo_master_meta_title', 'Мастерская обуви');
global $wp_rewrite;
$wp_rewrite->init();
flush_rewrite_rules(true);
echo json_encode(['entity_id'=>$id]);
""" % (json.dumps(admin_password), json.dumps(self.project_id), json.dumps(self.site_id)), installing=True)
            self.entity_id = int(json.loads(result)["entity_id"])
            self.copy_plugin(self.release_package)
            updated = self.php(r"""
require_once ABSPATH . 'wp-admin/includes/file.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
if (!defined('FS_METHOD')) { define('FS_METHOD', 'direct'); }
set_site_transient('update_plugins', (object)['response' => ['seo-master-connector/seo-master-plugin.php' => (object)[
    'package' => '/tmp/seo-package.zip', 'slug' => 'seo-master-connector', 'new_version' => SEO_MASTER_CONNECTOR_VERSION,
]]]);
ob_start();
$upgrader = new Plugin_Upgrader(new Automatic_Upgrader_Skin());
$result = $upgrader->upgrade('seo-master-connector/seo-master-plugin.php', ['clear_update_cache' => false]);
ob_end_clean();
if ($result !== true || !seo_master_verify_current_plugin_signature()) { throw new RuntimeException('signed_zip_upgrade_failed'); }
wp_set_current_user(get_user_by('login', 'fixture_admin')->ID);
$activated = activate_plugin('seo-master-connector/seo-master-plugin.php');
if (is_wp_error($activated) || !is_plugin_active('seo-master-connector/seo-master-plugin.php')) { throw new RuntimeException('signed_zip_reactivation_failed'); }
echo 'installed-and-updated';
""")
            if updated != 'installed-and-updated':
                raise RuntimeError('native ZIP install/update unexpected diagnostics')
            print('PASS WordPress Plugin_Upgrader: externally verified signed ZIP install and update; pinned ephemeral public key; exact installed tree.')
            print('Native installed package source_commit=' + self.source_commit + ' (all-zero means working-source fixture, not a release).')
            self.check_package_denials()
            # Rewrite generation during WP_INSTALLING omits normal post rules.
            # Rebuild after installation in a fresh, non-installing WordPress process.
            self.php("global $wp_rewrite; $wp_rewrite->init(); flush_rewrite_rules(true);")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check_package_denials(self) -> None:
        original = verify_package(self.release_package, self.release_keys)
        files = dict(original.files)
        files['seo-master-plugin.php'] = b"<?php file_put_contents('/tmp/ar15-candidate-executed','bad'); ?>\n" + files['seo-master-plugin.php']
        tampered = zip_bytes(files)
        try:
            verify_package(tampered, self.release_keys)
        except PackageError:
            pass
        else:
            raise AssertionError('external verifier accepted tampered candidate')
        self.transport_plugin(tampered)
        answer = self.php(r"""
require_once ABSPATH . 'wp-admin/includes/file.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
if (!defined('FS_METHOD')) { define('FS_METHOD', 'direct'); }
$plugin = 'seo-master-connector/seo-master-plugin.php';
$before = hash_file('sha256', SEO_MASTER_CONNECTOR_PATH . 'release-manifest.json');
$active = get_option('active_plugins');
$rejected_code = null;
add_filter('upgrader_source_selection', static function ($source) use (&$rejected_code) {
    if (is_wp_error($source)) { $rejected_code = $source->get_error_code(); }
    return $source;
}, 999);
set_site_transient('update_plugins', (object)['response' => [$plugin => (object)[
    'package' => '/tmp/seo-package.zip', 'slug' => 'seo-master-connector', 'new_version' => SEO_MASTER_CONNECTOR_VERSION,
]]]);
ob_start();
$result = (new Plugin_Upgrader(new Automatic_Upgrader_Skin()))->upgrade($plugin, ['clear_update_cache' => false]);
ob_end_clean();
if ($result === true || $rejected_code !== 'seo_master_plugin_signature' ||
    !seo_master_verify_current_plugin_signature() || hash_file('sha256', SEO_MASTER_CONNECTOR_PATH . 'release-manifest.json') !== $before ||
    file_exists('/tmp/ar15-candidate-executed')) { throw new RuntimeException('tampered_upgrade_not_denied:' . json_encode([
        'error'=>is_wp_error($result) ? $result->get_error_code() : gettype($result),
        'source_rejection'=>$rejected_code,
        'integrity'=>seo_master_verify_current_plugin_signature(),
        'same_manifest'=>hash_file('sha256', SEO_MASTER_CONNECTOR_PATH . 'release-manifest.json') === $before,
        'candidate_executed'=>file_exists('/tmp/ar15-candidate-executed'),
    ])); }
// Core manual upgrade deactivates in pre_install, before source_selection.
// The supported external verifier rejects before that point; this deliberate
// bypass tests only the internal second barrier and exact old-tree retention.
wp_set_current_user(get_user_by('login', 'fixture_admin')->ID);
$activated = activate_plugin($plugin);
if (is_wp_error($activated) || get_option('active_plugins') !== $active) { throw new RuntimeException('old_release_reactivation_failed'); }
echo 'denied';
""")
        if answer != 'denied':
            raise AssertionError('tampered update unexpected diagnostics')
        # Fresh process: no trust is visible even during initial plugin include.
        source = r"""<?php
putenv('SEO_MASTER_RELEASE_KEYS_JSON=');
define('WP_DISABLE_FATAL_ERROR_HANDLER', true);
$_SERVER['HTTP_HOST']='wordpress.test'; $_SERVER['REQUEST_URI']='/';
require '/var/www/html/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
$plugin = 'seo-master-connector/seo-master-plugin.php';
wp_set_current_user(get_user_by('login', 'fixture_admin')->ID);
deactivate_plugins($plugin, true);
add_filter('wp_die_handler', static fn() => static function () { throw new RuntimeException('fixture_activation_denied'); });
$denied = false;
try { activate_plugin($plugin); }
catch (RuntimeException $error) { if ($error->getMessage() !== 'fixture_activation_denied') { throw $error; } $denied = true; }
if (!$denied || is_plugin_active($plugin)) { throw new RuntimeException('missing_trust_activation_not_denied'); }
echo 'denied';
"""
        if self.run('exec', '-i', self.wp, 'php', stdin=source) != 'denied':
            raise AssertionError('untrusted activation unexpected diagnostics')
        self.php("require_once ABSPATH . 'wp-admin/includes/plugin.php'; wp_set_current_user(get_user_by('login','fixture_admin')->ID); $r=activate_plugin('seo-master-connector/seo-master-plugin.php'); if (is_wp_error($r) || !is_plugin_active('seo-master-connector/seo-master-plugin.php')) { throw new RuntimeException('fixture_reactivation_failed'); }")
        print('PASS native package denials: tampered update rejected before candidate execution; installed hashes preserved and old release reactivated; missing-trust activation explicitly denied.')

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
