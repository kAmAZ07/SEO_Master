<?php
declare(strict_types=1);
define('ABSPATH', __DIR__);
require __DIR__ . '/package-fixture.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/signature-verifier.php';
$root = package_fixture();
$count = 0;
function package_assert(bool $valid): void {
    global $count;
    ++$count;
    if (!$valid) { throw new RuntimeException('release_integrity_contract_failed_' . $count); }
}
package_assert(seo_master_verify_release_tree($root));
$trust = getenv('SEO_MASTER_RELEASE_KEYS_JSON');
foreach (['', '{}', '{"invalid":"invalid"}'] as $untrusted) {
    putenv('SEO_MASTER_RELEASE_KEYS_JSON=' . $untrusted);
    package_assert(!seo_master_verify_release_tree($root));
}
putenv('SEO_MASTER_RELEASE_KEYS_JSON=' . $trust);
$manifest_file = $root . '/release-manifest.json';
$original_manifest = file_get_contents($manifest_file);
$manifest = seo_master_release_json($original_manifest);
$manifest->source_commit = str_repeat('b', 40);
file_put_contents($manifest_file, seo_master_release_canonical($manifest));
package_assert(!seo_master_verify_release_tree($root));
file_put_contents($manifest_file, $original_manifest);
$signature_file = $root . '/release-signature.json';
$original_signature = file_get_contents($signature_file);
$envelope = seo_master_release_json($original_signature);
$envelope->signature = base64_encode(random_bytes(64));
file_put_contents($signature_file, seo_master_release_canonical($envelope));
package_assert(!seo_master_verify_release_tree($root));
file_put_contents($signature_file, $original_signature);
foreach (array_merge(seo_master_release_files(), ['release-manifest.json', 'release-signature.json']) as $path) {
    $file = $root . '/' . $path;
    $before = file_get_contents($file);
    file_put_contents($file, $before . ' ');
    package_assert(!seo_master_verify_release_tree($root));
    file_put_contents($file, $before);
    unlink($file);
    package_assert(!seo_master_verify_release_tree($root));
    file_put_contents($file, $before);
}
foreach (['extra.php', '.user.ini', '.htaccess', 'admin/extra.js', 'public-key.json'] as $path) {
    file_put_contents($root . '/' . $path, 'injected');
    package_assert(!seo_master_verify_release_tree($root));
    unlink($root . '/' . $path);
}
mkdir($root . '/unexpected');
package_assert(!seo_master_verify_release_tree($root));
rmdir($root . '/unexpected');
package_assert(seo_master_verify_release_tree($root));
printf("PHP release integrity: %d assertions passed (native sodium, disposable source fixture; not WP install)\n", $count);
