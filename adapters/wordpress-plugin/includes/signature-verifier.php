<?php
if (!defined('ABSPATH')) {
    exit;
}

/** Initial trust must be established outside the package, before executing PHP. */
function seo_master_release_files() {
    return [
        'admin/css/admin-styles.css', 'admin/js/admin-scripts.js', 'admin/settings.php',
        'includes/api-handler.php', 'includes/compatibility-checker.php', 'includes/hmac-validator.php',
        'includes/meta-filter.php', 'includes/signature-verifier.php', 'includes/v2-protocol.php',
        'includes/v2-service.php', 'includes/v2-sql.php', 'includes/v2-store.php',
        'readme.txt', 'seo-master-plugin.php', 'uninstall.php',
    ];
}

function seo_master_release_canonical($value) {
    if ($value instanceof stdClass) {
        $fields = get_object_vars($value);
        ksort($fields, SORT_STRING);
        $parts = [];
        foreach ($fields as $key => $entry) {
            $parts[] = json_encode($key, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . ':' . seo_master_release_canonical($entry);
        }
        return '{' . implode(',', $parts) . '}';
    }
    if (is_array($value)) { return '[' . implode(',', array_map('seo_master_release_canonical', $value)) . ']'; }
    return json_encode($value, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
}

function seo_master_release_json($bytes) {
    $value = json_decode($bytes, false, 16, JSON_THROW_ON_ERROR);
    if (!($value instanceof stdClass) || seo_master_release_canonical($value) !== $bytes) { throw new RuntimeException('release_json_invalid'); }
    return $value;
}

function seo_master_release_keys() {
    $raw = defined('SEO_MASTER_RELEASE_KEYS_JSON') ? SEO_MASTER_RELEASE_KEYS_JSON : getenv('SEO_MASTER_RELEASE_KEYS_JSON');
    if (!is_string($raw) || strlen($raw) > 8192) { return []; }
    $value = json_decode($raw, false, 4, JSON_THROW_ON_ERROR);
    if (!($value instanceof stdClass)) { return []; }
    $keys = get_object_vars($value);
    if (count($keys) < 1 || count($keys) > 4) { return []; }
    foreach ($keys as $id => $encoded) {
        if (!is_string($encoded)) { return []; }
        $key = base64_decode($encoded, true);
        if ($key === false || strlen($key) !== 32 || base64_encode($key) !== $encoded || hash('sha256', $key) !== $id) { return []; }
        $keys[$id] = $key;
    }
    return $keys;
}

function seo_master_verify_release_tree($directory) {
    try {
        if (!function_exists('sodium_crypto_sign_verify_detached') || is_link($directory) || !is_dir($directory)) { return false; }
        $root = realpath($directory);
        if ($root === false) { return false; }
        $files = [];
        $payload = seo_master_release_files();
        $allowed = array_merge($payload, ['release-manifest.json', 'release-signature.json']);
        $total = 0;
        $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::SELF_FIRST);
        foreach ($iterator as $entry) {
            $name = str_replace(DIRECTORY_SEPARATOR, '/', substr($entry->getPathname(), strlen($root) + 1));
            if ($entry->isLink()) { return false; }
            if ($entry->isDir()) {
                if (!in_array($name, ['admin', 'admin/css', 'admin/js', 'includes'], true)) { return false; }
                continue;
            }
            $limit = $name === 'release-manifest.json' ? 16384 : ($name === 'release-signature.json' ? 512 : 262144);
            $stat = lstat($entry->getPathname());
            if (!in_array($name, $allowed, true) || !$entry->isFile() || $stat === false || $stat['nlink'] !== 1 || $entry->getSize() < 1 || $entry->getSize() > $limit) { return false; }
            $bytes = file_get_contents($entry->getPathname(), false, null, 0, $limit + 1);
            if ($bytes === false || strlen($bytes) > $limit) { return false; }
            $total += strlen($bytes);
            if ($total > 2114152) { return false; }
            $files[$name] = $bytes;
        }
        $names = array_keys($files); sort($names, SORT_STRING); sort($allowed, SORT_STRING);
        if ($names !== $allowed) { return false; }
        $manifest = seo_master_release_json($files['release-manifest.json']);
        $envelope = seo_master_release_json($files['release-signature.json']);
        if (array_keys(get_object_vars($manifest)) !== ['files', 'key_id', 'plugin_id', 'plugin_version', 'schema_version', 'source_commit'] ||
            array_keys(get_object_vars($envelope)) !== ['algorithm', 'key_id', 'signature'] || $manifest->schema_version !== 1 ||
            $manifest->plugin_id !== 'seo-master-connector' || $envelope->algorithm !== 'ed25519' ||
            !is_string($manifest->key_id) || $envelope->key_id !== $manifest->key_id ||
            !is_string($manifest->source_commit) || preg_match('/\A[0-9a-f]{40}\z/D', $manifest->source_commit) !== 1 ||
            !is_string($envelope->signature)) { return false; }
        $keys = seo_master_release_keys();
        if (!isset($keys[$manifest->key_id])) { return false; }
        $signature = base64_decode($envelope->signature, true);
        if ($signature === false || strlen($signature) !== 64 || base64_encode($signature) !== $envelope->signature ||
            !sodium_crypto_sign_verify_detached($signature, "SEO-MASTER-PLUGIN-RELEASE-V1\n" . $files['release-manifest.json'], $keys[$manifest->key_id])) { return false; }
        $expected = [];
        foreach ($payload as $path) { $expected[] = (object) ['path' => $path, 'sha256' => hash('sha256', $files[$path]), 'size' => strlen($files[$path])]; }
        if (seo_master_release_canonical($manifest->files) !== seo_master_release_canonical($expected)) { return false; }
        if (preg_match('/^ \* Version: ([0-9]+[.][0-9]+[.][0-9]+(?:-[A-Za-z0-9.-]+)?)$/m', $files['seo-master-plugin.php'], $header) !== 1 ||
            preg_match('/^Stable tag: ([^\r\n]+)$/m', $files['readme.txt'], $readme) !== 1 ||
            preg_match("/define\('SEO_MASTER_CONNECTOR_VERSION', '([^']+)'\);/", $files['seo-master-plugin.php'], $constant) !== 1 ||
            strlen($header[1]) > 32 || $header[1] !== $readme[1] || $header[1] !== $constant[1] || $header[1] !== $manifest->plugin_version) { return false; }
        return true;
    } catch (Throwable $error) {
        // No optional SHA fallback, partial success, path or key diagnostics.
        return false;
    }
}

function seo_master_verify_current_plugin_signature() { return seo_master_verify_release_tree(SEO_MASTER_CONNECTOR_PATH); }

function seo_master_verify_plugin_update_signature($source, $remote_source, $upgrader, $hook_extra) {
    $plugin = $hook_extra['plugin'] ?? '';
    if ($plugin !== plugin_basename(SEO_MASTER_CONNECTOR_PATH . 'seo-master-plugin.php') && basename(rtrim($source, '/\\')) !== 'seo-master-connector') { return $source; }
    return seo_master_verify_release_tree($source) ? $source : new WP_Error('seo_master_plugin_signature', 'SEO Master release integrity verification failed.');
}
