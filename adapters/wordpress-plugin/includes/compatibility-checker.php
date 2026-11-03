<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Minimum API requirements only; runtime compatibility is not yet certified.
 */
if (!defined('SEO_MASTER_MIN_PHP_VERSION')) {
    define('SEO_MASTER_MIN_PHP_VERSION', '8.0');
}

if (!defined('SEO_MASTER_SUPPORTED_WP_MIN')) {
    define('SEO_MASTER_SUPPORTED_WP_MIN', '6.7');
}


function seo_master_get_supported_wordpress_versions() {
    return array();
}

function seo_master_is_wordpress_version_supported($version) {
    return version_compare((string) $version, SEO_MASTER_SUPPORTED_WP_MIN, '>=');
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
            /* translators: 1: PHP version, 2: WordPress min version. */
            __('SEO Master Connector requires PHP %1$s+ and WordPress %2$s+. Runtime compatibility is not certified.', 'seo-master-connector'),
            SEO_MASTER_MIN_PHP_VERSION,
            SEO_MASTER_SUPPORTED_WP_MIN
        )
    );
    echo '</p></div>';
}
