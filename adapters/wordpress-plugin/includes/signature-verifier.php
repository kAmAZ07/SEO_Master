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

function seo_master_get_plugin_signature() {
    if (defined('SEO_MASTER_PLUGIN_SIGNATURE')) {
        return (string) SEO_MASTER_PLUGIN_SIGNATURE;
    }

    $env_value = getenv('SEO_MASTER_PLUGIN_SIGNATURE');
    if ($env_value !== false && $env_value !== '') {
        return (string) $env_value;
    }

    return isset($_ENV['SEO_MASTER_PLUGIN_SIGNATURE']) ? (string) $_ENV['SEO_MASTER_PLUGIN_SIGNATURE'] : '';
}

function seo_master_verify_current_plugin_signature() {
    $signature = trim(seo_master_get_plugin_signature());
    if ($signature === '') {
        return true;
    }

    return seo_master_verify_plugin_signature(SEO_MASTER_CONNECTOR_PATH . 'seo-master-plugin.php', $signature);
}

function seo_master_verify_plugin_update_signature($source, $remote_source, $upgrader, $hook_extra) {
    $signature = trim(seo_master_get_plugin_signature());
    if ($signature === '') {
        return $source;
    }

    $plugin = isset($hook_extra['plugin']) ? (string) $hook_extra['plugin'] : '';
    if ($plugin !== '' && $plugin !== plugin_basename(SEO_MASTER_CONNECTOR_PATH . 'seo-master-plugin.php')) {
        return $source;
    }

    $main_file = trailingslashit($source) . 'seo-master-plugin.php';
    if (!seo_master_verify_plugin_signature($main_file, $signature)) {
        return new WP_Error(
            'seo_master_plugin_signature',
            __('SEO Master Connector package signature verification failed.', 'seo-master-connector')
        );
    }

    return $source;
}
