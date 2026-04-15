<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Read request headers from $_SERVER with Apache/Nginx compatibility.
 */
function seo_master_get_header($name) {
    $server_key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    if (isset($_SERVER[$server_key])) {
        return sanitize_text_field(wp_unslash($_SERVER[$server_key]));
    }

    return '';
}

/**
 * Parse timestamp provided as unix seconds or ISO-8601.
 */
function seo_master_parse_timestamp($raw_timestamp) {
    $value = trim((string) $raw_timestamp);
    if ($value === '') {
        return null;
    }

    if (ctype_digit($value)) {
        return (int) $value;
    }

    $parsed = strtotime($value);
    if ($parsed === false) {
        return null;
    }

    return (int) $parsed;
}

function seo_master_get_env_value($names) {
    foreach ((array) $names as $name) {
        if (defined($name)) {
            return (string) constant($name);
        }

        $env_value = getenv($name);
        if ($env_value !== false && $env_value !== '') {
            return (string) $env_value;
        }

        if (isset($_ENV[$name]) && $_ENV[$name] !== '') {
            return (string) $_ENV[$name];
        }
    }

    return '';
}

function seo_master_get_hmac_secret() {
    return seo_master_get_env_value(array('SEO_MASTER_HMAC_SECRET', 'WORDPRESS_HMAC_SECRET'));
}

/**
 * Validate HMAC signature and request freshness.
 */
function seo_master_validate_hmac($request_body, $method, $path) {
    $project_id = seo_master_get_header('X-Project-ID');
    $timestamp = seo_master_get_header('X-Timestamp');
    $signature = seo_master_get_header('X-Signature');

    if ($project_id === '' || $timestamp === '' || $signature === '') {
        return new WP_Error('seo_master_auth', 'Missing HMAC headers', array('status' => 401));
    }

    $configured_project_id = (string) get_option('seo_master_project_id', '');
    if ($configured_project_id !== '' && !hash_equals($configured_project_id, $project_id)) {
        return new WP_Error('seo_master_auth', 'Project ID mismatch', array('status' => 403));
    }

    $secret = seo_master_get_hmac_secret();
    if ($secret === '') {
        return new WP_Error('seo_master_auth', 'HMAC secret is not configured in env/wp-config', array('status' => 503));
    }

    $max_drift = (int) get_option('seo_master_max_drift', 300);
    if ($max_drift <= 0) {
        $max_drift = 300;
    }

    $timestamp_value = seo_master_parse_timestamp($timestamp);
    if ($timestamp_value === null) {
        return new WP_Error('seo_master_auth', 'Invalid timestamp format', array('status' => 401));
    }

    if (abs(time() - $timestamp_value) > $max_drift) {
        return new WP_Error('seo_master_auth', 'Timestamp drift too large', array('status' => 401));
    }

    $normalized_signature = preg_replace('/^sha256=/i', '', $signature);
    $body_hash = hash('sha256', (string) $request_body);
    $message = (string) $timestamp . strtoupper((string) $method) . (string) $path . $body_hash;
    $expected = hash_hmac('sha256', $message, $secret);

    if (!hash_equals($expected, $normalized_signature)) {
        return new WP_Error('seo_master_auth', 'Invalid signature', array('status' => 401));
    }

    return true;
}
