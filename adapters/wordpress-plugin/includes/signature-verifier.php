<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Verifies a SHA256 signature against a local file.
 */
function seo_master_verify_plugin_signature($file_path, $signature) {
    if (empty($file_path) || empty($signature) || !file_exists($file_path)) {
        return false;
    }

    $contents = file_get_contents($file_path);
    if ($contents === false) {
        return false;
    }

    $hash = hash('sha256', $contents);
    return hash_equals($hash, $signature);
}
