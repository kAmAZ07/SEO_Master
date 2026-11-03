<?php
if (!defined('ABSPATH')) { exit; }

function seo_master_register_settings_page() {
    add_options_page('SEO Master Connector', 'SEO Master Connector', 'manage_options', 'seo-master-connector', 'seo_master_render_settings_page');
}
function seo_master_sanitize_pairing_id($value) {
    // Invalid/missing input clears the binding rather than retaining an accidentally permissive project.
    return sm_v2_uuid($value) ? $value : '';
}
function seo_master_register_settings() {
    foreach (array('seo_master_project_id', 'seo_master_site_id') as $option) {
        register_setting('seo_master_settings', $option, array(
            'type' => 'string', 'sanitize_callback' => 'seo_master_sanitize_pairing_id', 'default' => '', 'show_in_rest' => false,
        ));
    }
}
function seo_master_render_settings_page() {
    if (!current_user_can('manage_options')) { return; }
    $paired = false;
    try { seo_master_v2_config(); $paired = true; }
    catch (SM_V2_Error $error) { $paired = false; }
    ?>
    <div class="wrap">
        <h1>SEO Master Connector v2</h1>
        <p><?php echo esc_html($paired ? 'Привязка настроена; готовность хранилища проверяется отдельно.' : 'Привязка неполная. Подписанные операции закрыты.'); ?></p>
        <p>Публикация разрешается только доверенному Gateway, который проверяет пользователя, одобрение, его срок и хеш предложения. Плагин самостоятельно HITL не подтверждает.</p>
        <form method="post" action="options.php">
            <?php settings_fields('seo_master_settings'); ?>
            <table class="form-table" role="presentation">
                <?php foreach (array('seo_master_project_id' => 'Project UUID', 'seo_master_site_id' => 'Site UUID') as $option => $label) { ?>
                    <tr><th scope="row"><label for="<?php echo esc_attr($option); ?>"><?php echo esc_html($label); ?></label></th>
                    <td><input class="regular-text" required maxlength="36" name="<?php echo esc_attr($option); ?>" id="<?php echo esc_attr($option); ?>" value="<?php echo esc_attr(get_option($option, '')); ?>" /></td></tr>
                <?php } ?>
            </table>
            <?php submit_button(); ?>
        </form>
        <p>Ключи задаются только через окружение / wp-config: SEO_MASTER_KEY_ID, SEO_MASTER_HMAC_SECRET и отдельный SEO_MASTER_RECEIPT_SECRET. Секреты здесь не отображаются и в options не сохраняются.</p>
        <p>Флаг SEO_MASTER_TRUSTED_GATEWAY=1 включается только после подключения проверяющего HITL Gateway. Пока AR-4 не интегрирован, оставьте публикацию выключенной.</p>
        <p>Схема v2 не устанавливается автоматически. Активация с SEO_MASTER_INSTALL_V2_SCHEMA=1 допустима лишь на отдельно согласованном изолированном стенде; после установки удалите флаг. Инструкции и ограничения: docs/WORDPRESS.md.</p>
        <code><?php echo esc_html(home_url('/wp-json/seo-master/v2/capabilities')); ?></code>
    </div>
    <?php
}
