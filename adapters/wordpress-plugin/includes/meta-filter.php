<?php
if (!defined('ABSPATH')) {
    exit;
}

function seo_master_get_frontend_post_id() {
    if (!is_singular()) {
        return 0;
    }

    $post_id = get_queried_object_id();
    return $post_id ? (int) $post_id : 0;
}

function seo_master_get_managed_meta($post_id, $meta_key) {
    $value = get_post_meta((int) $post_id, $meta_key, true);
    return is_string($value) ? $value : '';
}

function seo_master_get_managed_title($post_id = 0) {
    $post_id = $post_id ? (int) $post_id : seo_master_get_frontend_post_id();
    if (!$post_id) {
        return '';
    }

    $value = seo_master_get_managed_meta($post_id, '_seo_master_meta_title');
    try { return sm_v2_text($value, 512); }
    catch (SM_V2_Error $error) { return ''; }
}

function seo_master_get_managed_description($post_id = 0) {
    $post_id = $post_id ? (int) $post_id : seo_master_get_frontend_post_id();
    if (!$post_id) {
        return '';
    }

    $value = seo_master_get_managed_meta($post_id, '_seo_master_meta_description');
    try { return sm_v2_text($value, 2000); }
    catch (SM_V2_Error $error) { return ''; }
}

function seo_master_get_managed_h1($post_id = 0) {
    $post_id = $post_id ? (int) $post_id : seo_master_get_frontend_post_id();
    if (!$post_id) {
        return '';
    }

    return seo_master_get_managed_meta($post_id, '_seo_master_h1');
}

function seo_master_get_managed_schema_json($post_id = 0) {
    $post_id = $post_id ? (int) $post_id : seo_master_get_frontend_post_id();
    if (!$post_id) {
        return '';
    }

    return seo_master_get_managed_meta($post_id, '_seo_master_schema_jsonld');
}

function seo_master_get_managed_schema_array($post_id = 0) {
    $schema_json = seo_master_get_managed_schema_json($post_id);
    if ($schema_json === '') {
        return null;
    }

    try {
        sm_v2_jsonld($schema_json);
        return json_decode($schema_json, true);
    } catch (SM_V2_Error $error) { return null; }
}

function seo_master_has_yoast_seo() {
    return defined('WPSEO_VERSION') || class_exists('WPSEO_Options') || function_exists('wpseo_init');
}

function seo_master_has_rank_math() {
    return defined('RANK_MATH_VERSION') || class_exists('RankMath') || function_exists('rank_math');
}

function seo_master_has_supported_seo_plugin() {
    return seo_master_has_yoast_seo() || seo_master_has_rank_math();
}

function seo_master_filter_document_title($title) {
    $managed_title = seo_master_get_managed_title();
    return $managed_title !== '' ? $managed_title : $title;
}

function seo_master_filter_document_title_parts($parts) {
    $managed_title = seo_master_get_managed_title();
    if ($managed_title !== '') {
        $parts['title'] = $managed_title;
    }

    return $parts;
}

function seo_master_filter_seo_title($title) {
    $managed_title = seo_master_get_managed_title();
    return $managed_title !== '' ? $managed_title : $title;
}

function seo_master_filter_seo_description($description) {
    $managed_description = seo_master_get_managed_description();
    return $managed_description !== '' ? $managed_description : $description;
}

function seo_master_filter_h1_title($title, $post_id = 0) {
    return $title;
}

function seo_master_filter_yoast_schema_graph($graph) {
    // JSON-LD with an SEO plugin is manual until a real merge/conflict compatibility matrix is verified.
    return $graph;
}

function seo_master_filter_rank_math_schema($schema_graph) {
    return $schema_graph;
}

/**
 * Output fallback meta description and JSON-LD when no supported SEO plugin owns the head.
 */
function seo_master_output_meta_tags() {
    if (!is_singular() || seo_master_has_supported_seo_plugin()) {
        return;
    }

    $post_id = seo_master_get_frontend_post_id();
    if (!$post_id) {
        return;
    }

    $meta_description = seo_master_get_managed_description($post_id);
    if ($meta_description !== '') {
        echo '<meta name="description" content="' . esc_attr($meta_description) . '" />' . "\n";
    }

    $schema = seo_master_get_managed_schema_array($post_id);
    if ($schema) {
        echo '<script type="application/ld+json">' . wp_json_encode($schema, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_UNESCAPED_UNICODE) . '</script>' . "\n";
    }
}
