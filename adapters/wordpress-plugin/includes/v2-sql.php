<?php
declare(strict_types=1);
if (!defined('ABSPATH')) { exit; }

interface SM_V2_SQL {
    public function tables(): array;
    public function character_set(): string;
    public function query(string $sql, array $identifiers = [], array $parameters = []): array|int;
}

final class SM_V2_Mysqli implements SM_V2_SQL {
    private array $allowed_tables;
    public function __construct(private wpdb $wpdb, private mysqli $connection) {
        $prefix = $wpdb->prefix;
        if (!is_string($prefix) || preg_match('/\A[A-Za-z0-9_]{1,40}\z/D', $prefix) !== 1 ||
            $wpdb->posts !== $prefix . 'posts' || $wpdb->postmeta !== $prefix . 'postmeta' ||
            $wpdb->dbh !== $connection || !function_exists('mysqli_stmt_get_result') ||
            (defined('WP_CONTENT_DIR') && file_exists(WP_CONTENT_DIR . '/db.php'))) {
            throw new SM_V2_Error('unsupported_storage', 503);
        }
        $this->allowed_tables = [
            'posts' => $wpdb->posts, 'meta' => $wpdb->postmeta,
            'operations' => $prefix . 'seo_master_operations_v2', 'nonces' => $prefix . 'seo_master_nonces_v2',
        ];
    }
    public function tables(): array { return $this->allowed_tables; }
    public function character_set(): string { return mysqli_character_set_name($this->connection); }

    // Never use wpdb::query(): its reconnect+replay can run a transactional write again in autocommit.
    public function query(string $sql, array $identifiers = [], array $parameters = []): array|int {
        foreach ($identifiers as $identifier) {
            if (!in_array($identifier, $this->allowed_tables, true)) { throw new SM_V2_Error('unsupported_storage', 503); }
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
