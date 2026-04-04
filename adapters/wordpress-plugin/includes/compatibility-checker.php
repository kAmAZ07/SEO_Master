<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Check minimal compatibility requirements.
 */
function seo_master_is_compatible() {
    global $wp_version;

    if (version_compare(PHP_VERSION, '8.0', '<')) {
        return false;
    }

    if (version_compare($wp_version, '6.4', '<')) {
        return false;
    }

    return true;
}

/**
 * Render admin notice when plugin requirements are not met.
 */
function seo_master_render_incompatibility_notice() {
    echo '<div class="notice notice-error"><p>';
    echo esc_html__('SEO Master Connector requires PHP 8.0+ and WordPress 6.4+.', 'seo-master-connector');
    echo '</p></div>';
}
