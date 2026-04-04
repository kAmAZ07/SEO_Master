=== SEO Master Connector ===
Contributors: seo-master
Requires at least: 6.4
Tested up to: 6.6
Requires PHP: 8.0
Stable tag: 0.2.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Secure connector that receives HMAC-signed SEO patch updates from SEO Master.

== Description ==
SEO Master Connector exposes REST endpoints for SEO changes:
- `/wp-json/seo-master/v1/meta`
- `/wp-json/seo-master/v1/schema`
- `/wp-json/seo-master/v1/interlinks`

Requests are protected with HMAC headers:
- `X-Project-ID`
- `X-Timestamp`
- `X-Signature`

== Installation ==
1. Upload the plugin folder to `/wp-content/plugins/`.
2. Activate **SEO Master Connector** in WordPress admin.
3. Open **Settings -> SEO Master Connector**.
4. Set Project ID and HMAC Secret shared with SEO Master platform.

== Changelog ==
= 0.2.0 =
* Added dedicated endpoints for meta/schema/interlinks.
* Added timestamp drift validation and project binding.
* Added admin settings page with configuration form.
* Added meta description and JSON-LD output hook.

= 0.1.0 =
* Initial draft.
