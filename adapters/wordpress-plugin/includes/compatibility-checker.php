<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Compatibility policy: support the latest three tested WordPress minors.
 */
if (!defined('SEO_MASTER_MIN_PHP_VERSION')) {
    define('SEO_MASTER_MIN_PHP_VERSION', '8.0');
}

if (!defined('SEO_MASTER_SUPPORTED_WP_MIN')) {
    define('SEO_MASTER_SUPPORTED_WP_MIN', '6.7');
}

if (!defined('SEO_MASTER_SUPPORTED_WP_MAX')) {
    define('SEO_MASTER_SUPPORTED_WP_MAX', '6.9');
}

function seo_master_normalize_wp_minor_version($version) {
    $parts = explode('.', preg_replace('/[^0-9.].*/', '', (string) $version));
    $major = isset($parts[0]) && $parts[0] !== '' ? $parts[0] : '0';
    $minor = isset($parts[1]) && $parts[1] !== '' ? $parts[1] : '0';

    return $major . '.' . $minor;
}

function seo_master_get_supported_wordpress_versions() {
    return array('6.7', '6.8', '6.9');
}

function seo_master_is_wordpress_version_supported($version) {
    $minor_version = seo_master_normalize_wp_minor_version($version);

    return in_array($minor_version, seo_master_get_supported_wordpress_versions(), true);
}

function seo_master_is_compatible() {
    global $wp_version;

    if (version_compare(PHP_VERSION, SEO_MASTER_MIN_PHP_VERSION, '<')) {
        return false;
    }

    if (!seo_master_is_wordpress_version_supported($wp_version)) {
        return false;
    }

    return true;
}

/**
 * Render admin notice when plugin requirements are not met.
 */
function seo_master_render_incompatibility_notice() {
    echo '<div class="notice notice-error"><p>';
    echo esc_html(
        sprintf(
            /* translators: 1: PHP version, 2: WordPress min version, 3: WordPress max version. */
            __('SEO Master Connector requires PHP %1$s+ and supports WordPress %2$s-%3$s.', 'seo-master-connector'),
            SEO_MASTER_MIN_PHP_VERSION,
            SEO_MASTER_SUPPORTED_WP_MIN,
            SEO_MASTER_SUPPORTED_WP_MAX
        )
    );
    echo '</p></div>';
}
