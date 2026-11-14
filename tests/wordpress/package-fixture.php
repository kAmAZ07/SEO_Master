<?php
declare(strict_types=1);

// Synthetic ephemeral signing authority, only for standalone PHP fixtures.
function package_fixture(): string {
    $paths = [
        'admin/css/admin-styles.css', 'admin/js/admin-scripts.js', 'admin/settings.php',
        'includes/api-handler.php', 'includes/compatibility-checker.php', 'includes/hmac-validator.php',
        'includes/meta-filter.php', 'includes/signature-verifier.php', 'includes/v2-protocol.php',
        'includes/v2-service.php', 'includes/v2-sql.php', 'includes/v2-store.php',
        'readme.txt', 'seo-master-plugin.php', 'uninstall.php',
    ];
    $root = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'seo-package-' . bin2hex(random_bytes(16));
    if (!mkdir($root, 0700)) { throw new RuntimeException('fixture_directory'); }
    register_shutdown_function(static function () use ($root): void {
        // Only this exclusively created random fixture root is removed, never linked trees.
        $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
        foreach ($iterator as $entry) {
            if ($entry->isDir() && !$entry->isLink()) { rmdir($entry->getPathname()); }
            else { unlink($entry->getPathname()); }
        }
        rmdir($root);
    });
    $entries = [];
    foreach ($paths as $path) {
        $bytes = str_replace("\r\n", "\n", file_get_contents(__DIR__ . '/../../adapters/wordpress-plugin/' . $path));
        $target = $root . '/' . $path;
        if (!is_dir(dirname($target))) { mkdir(dirname($target), 0700, true); }
        file_put_contents($target, $bytes);
        $entries[] = ['path' => $path, 'sha256' => hash('sha256', $bytes), 'size' => strlen($bytes)];
    }
    $keypair = sodium_crypto_sign_keypair();
    $public = sodium_crypto_sign_publickey($keypair);
    $id = hash('sha256', $public);
    preg_match('/^ \* Version: ([^\r\n]+)$/m', file_get_contents($root . '/seo-master-plugin.php'), $version);
    // Keys deliberately ordered independently to match the wire canonical encoding.
    $manifest = json_encode(['files' => $entries, 'key_id' => $id, 'plugin_id' => 'seo-master-connector',
        'plugin_version' => $version[1], 'schema_version' => 1, 'source_commit' => str_repeat('a', 40)], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    $signature = sodium_crypto_sign_detached("SEO-MASTER-PLUGIN-RELEASE-V1\n" . $manifest, sodium_crypto_sign_secretkey($keypair));
    file_put_contents($root . '/release-manifest.json', $manifest);
    file_put_contents($root . '/release-signature.json', json_encode(['algorithm' => 'ed25519', 'key_id' => $id,
        'signature' => base64_encode($signature)], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
    putenv('SEO_MASTER_RELEASE_KEYS_JSON=' . json_encode([$id => base64_encode($public)], JSON_THROW_ON_ERROR));
    return $root;
}
