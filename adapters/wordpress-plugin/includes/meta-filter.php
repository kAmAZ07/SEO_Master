<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Output meta description and JSON-LD from plugin-managed post meta.
 */
function seo_master_output_meta_tags() {
    if (!is_singular()) {
        return;
    }

    $post_id = get_queried_object_id();
    if (!$post_id) {
        return;
    }

    $meta_description = get_post_meta($post_id, '_seo_master_meta_description', true);
    if (!empty($meta_description)) {
        echo '<meta name="description" content="' . esc_attr($meta_description) . '" />' . "\n";
    }

    $schema_json = get_post_meta($post_id, '_seo_master_schema_jsonld', true);
    if (!empty($schema_json)) {
        echo '<script type="application/ld+json">' . wp_kses_post($schema_json) . '</script>' . "\n";
    }
}
