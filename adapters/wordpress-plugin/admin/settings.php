<?php
if (!defined('ABSPATH')) {
    exit;
}

function seo_master_register_settings_page() {
    add_options_page(
        __('SEO Master Connector', 'seo-master-connector'),
        __('SEO Master Connector', 'seo-master-connector'),
        'manage_options',
        'seo-master-connector',
        'seo_master_render_settings_page'
    );
}

function seo_master_register_settings() {
    register_setting('seo_master_settings', 'seo_master_project_id', array(
        'type' => 'string',
        'sanitize_callback' => 'sanitize_text_field',
        'default' => '',
    ));

    register_setting('seo_master_settings', 'seo_master_hmac_secret', array(
        'type' => 'string',
        'sanitize_callback' => 'sanitize_text_field',
        'default' => '',
    ));

    register_setting('seo_master_settings', 'seo_master_max_drift', array(
        'type' => 'integer',
        'sanitize_callback' => 'absint',
        'default' => 300,
    ));
}

function seo_master_render_settings_page() {
    if (!current_user_can('manage_options')) {
        return;
    }
    ?>
    <div class="wrap">
        <h1><?php esc_html_e('SEO Master Connector', 'seo-master-connector'); ?></h1>
        <p><?php esc_html_e('Configure authentication and project binding for incoming SEO patches.', 'seo-master-connector'); ?></p>

        <form method="post" action="options.php">
            <?php settings_fields('seo_master_settings'); ?>
            <table class="form-table" role="presentation">
                <tr>
                    <th scope="row">
                        <label for="seo_master_project_id"><?php esc_html_e('Project ID', 'seo-master-connector'); ?></label>
                    </th>
                    <td>
                        <input name="seo_master_project_id" id="seo_master_project_id" type="text" class="regular-text" value="<?php echo esc_attr(get_option('seo_master_project_id', '')); ?>" />
                        <p class="description"><?php esc_html_e('Must match X-Project-ID from signed requests.', 'seo-master-connector'); ?></p>
                    </td>
                </tr>
                <tr>
                    <th scope="row">
                        <label for="seo_master_hmac_secret"><?php esc_html_e('HMAC Secret', 'seo-master-connector'); ?></label>
                    </th>
                    <td>
                        <input name="seo_master_hmac_secret" id="seo_master_hmac_secret" type="password" class="regular-text" value="<?php echo esc_attr(get_option('seo_master_hmac_secret', '')); ?>" autocomplete="off" />
                        <p class="description"><?php esc_html_e('Shared secret used to validate patch signatures.', 'seo-master-connector'); ?></p>
                    </td>
                </tr>
                <tr>
                    <th scope="row">
                        <label for="seo_master_max_drift"><?php esc_html_e('Max timestamp drift (seconds)', 'seo-master-connector'); ?></label>
                    </th>
                    <td>
                        <input name="seo_master_max_drift" id="seo_master_max_drift" type="number" min="10" step="1" class="small-text" value="<?php echo esc_attr(get_option('seo_master_max_drift', 300)); ?>" />
                    </td>
                </tr>
            </table>
            <?php submit_button(); ?>
        </form>

        <h2><?php esc_html_e('REST endpoints', 'seo-master-connector'); ?></h2>
        <code><?php echo esc_html(home_url('/wp-json/seo-master/v1/meta')); ?></code><br />
        <code><?php echo esc_html(home_url('/wp-json/seo-master/v1/schema')); ?></code><br />
        <code><?php echo esc_html(home_url('/wp-json/seo-master/v1/interlinks')); ?></code>
    </div>
    <?php
}
