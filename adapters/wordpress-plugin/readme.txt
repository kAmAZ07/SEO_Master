=== SEO Master Connector ===
Contributors: seo-master
Requires at least: 6.7
Tested up to: 6.9
Requires PHP: 8.0
Stable tag: 0.2.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Secure connector that receives HMAC-signed SEO patch updates from SEO Master.
Compatible with the latest three tested WordPress minor versions: 6.7, 6.8, and 6.9.

== Description ==
SEO Master Connector exposes REST endpoints for SEO changes:
- `/wp-json/seo-master/v1/meta`
- `/wp-json/seo-master/v1/schema`
- `/wp-json/seo-master/v1/interlinks`

Requests are protected with HMAC headers:
- `X-Project-ID`
- `X-Timestamp`
- `X-Signature`

The connector intercepts managed Title, Description, and Schema output through WordPress core, Yoast SEO, and RankMath filters.
Managed `/h1` patches are applied on singular templates through the main title filter.

== Installation ==
1. Upload the plugin folder to `/wp-content/plugins/`.
2. Activate **SEO Master Connector** in WordPress admin.
3. Open **Settings -> SEO Master Connector**.
4. Set Project ID.
5. Define `SEO_MASTER_HMAC_SECRET` in `wp-config.php` or the server environment.
6. Optionally define `SEO_MASTER_PLUGIN_SIGNATURE` to enforce package signature checks during activation/update.

== Changelog ==
= 0.2.0 =
* Added dedicated endpoints for meta/schema/interlinks.
* Added timestamp drift validation and project binding.
* Added admin settings page with configuration form.
* Moved HMAC secret loading to env/wp-config and added plugin signature verification hooks.
* Added Yoast/RankMath interception for title, description, and schema.
* Added H1 substitution for singular page output.

= 0.1.0 =
* Initial draft.
