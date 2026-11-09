<?php
declare(strict_types=1);
if (!defined('ABSPATH')) { exit; }

interface SM_V2_SQL {
    public function tables(): array;
    public function character_set(): string;
    public function query(string $sql, array $identifiers = [], array $parameters = []): array|int;
}

interface SM_V2_Target_SQL extends SM_V2_SQL {
    public function assert_target_url(int $entity_id, string $url): void;
}

final class SM_V2_Mysqli implements SM_V2_Target_SQL {
    private array $allowed_tables;
    private string $options_table;
    public function __construct(private wpdb $wpdb, private mysqli $connection) {
        $prefix = $wpdb->prefix;
        if (!is_string($prefix) || preg_match('/\A[A-Za-z0-9_]{1,40}\z/D', $prefix) !== 1 ||
            $wpdb->posts !== $prefix . 'posts' || $wpdb->postmeta !== $prefix . 'postmeta' || $wpdb->options !== $prefix . 'options' ||
            $wpdb->dbh !== $connection || !function_exists('mysqli_stmt_get_result') ||
            (defined('WP_CONTENT_DIR') && file_exists(WP_CONTENT_DIR . '/db.php'))) {
            throw new SM_V2_Error('unsupported_storage', 503);
        }
        $this->allowed_tables = [
            'posts' => $wpdb->posts, 'meta' => $wpdb->postmeta,
            'operations' => $prefix . 'seo_master_operations_v2', 'nonces' => $prefix . 'seo_master_nonces_v2',
        ];
        $this->options_table = $wpdb->options;
    }
    public function tables(): array { return $this->allowed_tables; }
    public function character_set(): string { return mysqli_character_set_name($this->connection); }

    public function assert_target_url(int $entity_id, string $url): void {
        // Called only inside the store's SERIALIZABLE transaction, after locking the post.
        // Never derive authorization from WP's object/option/rewrite caches.
        foreach (['pre_post_link', 'post_link', 'page_link', '_get_page_link', 'get_page_uri', 'home_url', 'user_trailingslashit',
                  'get_post_status', 'is_post_status_viewable', 'set_url_scheme'] as $hook) {
            if (has_filter($hook) !== false) { throw new SM_V2_Error('unsupported_target_binding', 422); }
        }
        foreach (['pre_option', 'pre_option_home', 'pre_option_permalink_structure', 'pre_option_show_on_front', 'pre_option_page_on_front',
                  'option_permalink_structure', 'option_show_on_front', 'option_page_on_front'] as $hook) {
            if (has_filter($hook) !== false) { throw new SM_V2_Error('unsupported_target_binding', 422); }
        }
        // WordPress installs this single core callback even without WP_HOME.
        global $wp_filter;
        foreach (($wp_filter['option_home']->callbacks ?? []) as $callbacks) {
            foreach ($callbacks as $callback) {
                if (($callback['function'] ?? null) !== '_config_wp_home') { throw new SM_V2_Error('unsupported_target_binding', 422); }
            }
        }
        $this->query('SELECT 1 FROM %i LIMIT 0', [$this->options_table]);
        $engines = $this->query('SELECT ENGINE, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?', [], [$this->options_table]);
        if (count($engines) !== 1 || strtoupper($engines[0]['ENGINE'] ?? '') !== 'INNODB' || ($engines[0]['TABLE_TYPE'] ?? '') !== 'BASE TABLE') {
            throw new SM_V2_Error('unsupported_storage', 503);
        }
        $rows = $this->query('SELECT option_name, option_value FROM %i WHERE option_name IN (?, ?, ?, ?) FOR UPDATE',
            [$this->options_table], ['home', 'permalink_structure', 'show_on_front', 'page_on_front']);
        $options = [];
        foreach ($rows as $row) { $options[$row['option_name']] = $row['option_value']; }
        if (count($options) !== 4 || !is_string($options['home']) ||
            (defined('WP_HOME') && WP_HOME !== $options['home']) ||
            preg_match('~\Ahttps?://[^/?#@\s]+(?:/[^?#\s]*)?\z~D', $options['home']) !== 1) {
            throw new SM_V2_Error('unsupported_target_binding', 422);
        }
        $posts = $this->query('SELECT ID, post_type, post_status, post_password, post_name, post_parent FROM %i WHERE ID = ? FOR UPDATE', [$this->allowed_tables['posts']], [$entity_id]);
        if (count($posts) !== 1 || $posts[0]['post_status'] !== 'publish' || $posts[0]['post_password'] !== '') {
            throw new SM_V2_Error('target_not_found', 404);
        }
        $post = $posts[0];
        $home = rtrim($options['home'], '/');
        $structure = $options['permalink_structure'];
        // Explicit supported subset. Taxonomy/author/custom rewrite contracts need a separate adapter.
        if (!in_array($structure, ['', '/%postname%/', '/%postname%', '/%post_id%/', '/%post_id%', '/index.php/%postname%/', '/index.php/%post_id%/'], true)) {
            throw new SM_V2_Error('unsupported_target_binding', 422);
        }
        if ($post['post_type'] === 'page' && $options['show_on_front'] === 'page' && (string) $entity_id === $options['page_on_front']) {
            $expected = $home . '/';
        } elseif ($structure === '') {
            $expected = $home . '/?' . ($post['post_type'] === 'page' ? 'page_id=' : 'p=') . $entity_id;
        } elseif ($post['post_type'] === 'post') {
            $this->assert_slug($post['post_name']);
            $expected = $home . str_replace(['%postname%', '%post_id%'], [$post['post_name'], (string) $entity_id], $structure);
        } elseif ($post['post_type'] === 'page') {
            $parts = [];
            $seen = [];
            for ($depth = 0; ; ++$depth) {
                $id = (int) $post['ID'];
                if ($depth >= 16 || isset($seen[$id]) || $post['post_type'] !== 'page') { throw new SM_V2_Error('unsupported_target_binding', 422); }
                $seen[$id] = true;
                $this->assert_slug($post['post_name']);
                array_unshift($parts, $post['post_name']);
                if ((int) $post['post_parent'] === 0) { break; }
                $parents = $this->query('SELECT ID, post_type, post_name, post_parent FROM %i WHERE ID = ? FOR UPDATE', [$this->allowed_tables['posts']], [(int) $post['post_parent']]);
                if (count($parents) !== 1) { throw new SM_V2_Error('unsupported_target_binding', 422); }
                $post = $parents[0];
            }
            $expected = $home . (str_starts_with($structure, '/index.php/') ? '/index.php/' : '/') . implode('/', $parts) . (str_ends_with($structure, '/') ? '/' : '');
        } else { throw new SM_V2_Error('unsupported_target_binding', 422); }
        if (!hash_equals($expected, $url)) { throw new SM_V2_Error('target_url_conflict', 409); }
    }

    private function assert_slug(mixed $slug): void {
        if (!is_string($slug) || $slug === '' || strlen($slug) > 600 || preg_match('~\A(?:[a-zA-Z0-9_-]|%[0-9a-fA-F]{2})+\z~D', $slug) !== 1) {
            throw new SM_V2_Error('unsupported_target_binding', 422);
        }
    }

    // Never use wpdb::query(): its reconnect+replay can run a transactional write again in autocommit.
    public function query(string $sql, array $identifiers = [], array $parameters = []): array|int {
        foreach ($identifiers as $identifier) {
            if (!in_array($identifier, [...array_values($this->allowed_tables), $this->options_table], true)) { throw new SM_V2_Error('unsupported_storage', 503); }
        }
        $prepared_sql = $identifiers === [] ? $sql : $this->wpdb->prepare($sql, $identifiers);
        if (!is_string($prepared_sql) || $prepared_sql === '') { throw new SM_V2_Error('storage_failure', 503); }
        $statement = null;
        try {
            if ($identifiers === [] && $parameters === [] && in_array($sql, [
                'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE', 'START TRANSACTION', 'COMMIT', 'ROLLBACK',
            ], true)) {
                if (!@mysqli_query($this->connection, $sql)) { throw new SM_V2_Error('storage_failure', 503); }
                return 0;
            }
            $statement = @mysqli_prepare($this->connection, $prepared_sql);
            if ($statement === false) { throw new SM_V2_Error('storage_failure', 503); }
            if ($parameters !== []) {
                $types = '';
                foreach ($parameters as $parameter) {
                    if (!is_int($parameter) && !is_string($parameter)) { throw new SM_V2_Error('storage_failure', 503); }
                    $types .= is_int($parameter) ? 'i' : 's';
                }
                if (!@$statement->bind_param($types, ...$parameters)) { throw new SM_V2_Error('storage_failure', 503); }
            }
            if (!@mysqli_stmt_execute($statement)) {
                throw new SM_V2_Error(mysqli_stmt_errno($statement) === 1062 ? 'duplicate_key' : 'storage_failure', 503);
            }
            $result = mysqli_stmt_get_result($statement);
            if ($result instanceof mysqli_result) {
                $rows = mysqli_fetch_all($result, MYSQLI_ASSOC);
                mysqli_free_result($result);
                return $rows;
            }
            return mysqli_stmt_affected_rows($statement);
        } catch (mysqli_sql_exception $error) {
            throw new SM_V2_Error($error->getCode() === 1062 ? 'duplicate_key' : 'storage_failure', 503);
        } finally {
            if ($statement instanceof mysqli_stmt) { mysqli_stmt_close($statement); }
        }
    }
}
