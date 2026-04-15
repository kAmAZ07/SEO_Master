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
    return is_string($value) ? trim($value) : '';
}

function seo_master_get_managed_title($post_id = 0) {
    $post_id = $post_id ? (int) $post_id : seo_master_get_frontend_post_id();
    if (!$post_id) {
        return '';
    }

    return seo_master_get_managed_meta($post_id, '_seo_master_meta_title');
}

function seo_master_get_managed_description($post_id = 0) {
    $post_id = $post_id ? (int) $post_id : seo_master_get_frontend_post_id();
    if (!$post_id) {
        return '';
    }

    return seo_master_get_managed_meta($post_id, '_seo_master_meta_description');
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

    $decoded = json_decode($schema_json, true);
    return is_array($decoded) ? $decoded : null;
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
    if (is_admin() || !is_singular() || !in_the_loop() || !is_main_query()) {
        return $title;
    }

    $queried_post_id = seo_master_get_frontend_post_id();
    if (!$queried_post_id || (int) $post_id !== $queried_post_id) {
        return $title;
    }

    $managed_h1 = seo_master_get_managed_h1($queried_post_id);
    return $managed_h1 !== '' ? $managed_h1 : $title;
}

function seo_master_filter_yoast_schema_graph($graph) {
    $schema = seo_master_get_managed_schema_array();
    if (!$schema) {
        return $graph;
    }

    if (isset($schema['@graph']) && is_array($schema['@graph'])) {
        return $schema['@graph'];
    }

    if (array_keys($schema) === range(0, count($schema) - 1)) {
        return $schema;
    }

    return array($schema);
}

function seo_master_filter_rank_math_schema($data) {
    $schema = seo_master_get_managed_schema_array();
    if (!$schema) {
        return $data;
    }

    $rank_math_schema = array();
    if (isset($schema['@graph']) && is_array($schema['@graph'])) {
        foreach ($schema['@graph'] as $index => $schema_node) {
            if (is_array($schema_node)) {
                $rank_math_schema['seo_master_' . (int) $index] = $schema_node;
            }
        }

        return !empty($rank_math_schema) ? $rank_math_schema : $data;
    }

    if (array_keys($schema) === range(0, count($schema) - 1)) {
        foreach ($schema as $index => $schema_node) {
            if (is_array($schema_node)) {
                $rank_math_schema['seo_master_' . (int) $index] = $schema_node;
            }
        }

        return !empty($rank_math_schema) ? $rank_math_schema : $data;
    }

    return array('seo_master' => $schema);
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
        echo '<script type="application/ld+json">' . wp_json_encode($schema, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . '</script>' . "\n";
    }
}
