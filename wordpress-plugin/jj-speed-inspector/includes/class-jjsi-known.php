<?php
/**
 * What we know about common plugins: advice for heavy ones, caching plugins, and how to tell which plugin owns
 * a database table, an option or a scheduled task.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_Known {

	/** Page caching plugins (folder name => name). */
	public static $cache_plugins = array(
		'wp-rocket'               => 'WP Rocket',
		'litespeed-cache'         => 'LiteSpeed Cache',
		'w3-total-cache'          => 'W3 Total Cache',
		'wp-super-cache'          => 'WP Super Cache',
		'wp-fastest-cache'        => 'WP Fastest Cache',
		'sg-cachepress'           => 'SiteGround Speed Optimizer',
		'breeze'                  => 'Breeze',
		'hummingbird-performance' => 'Hummingbird',
		'cache-enabler'           => 'Cache Enabler',
		'swift-performance-lite'  => 'Swift Performance',
		'comet-cache'             => 'Comet Cache',
		'wp-optimize'             => 'WP-Optimize',
		'flying-press'            => 'FlyingPress',
		'nitropack'               => 'NitroPack',
		'powered-cache'           => 'Powered Cache',
		'wp-cloudflare-page-cache' => 'Super Page Cache',
	);

	/** Plugins with a well known speed/server cost: folder => array( severity, advice ). */
	public static $advice = array(
		'query-monitor'          => array( 'warning', 'Query Monitor is a developer tool. It slows every page and should be deactivated when you are not actively debugging.' ),
		'debug-bar'              => array( 'warning', 'Debug Bar is a developer tool - deactivate it on a live site.' ),
		'broken-link-checker'    => array( 'warning', 'Broken Link Checker is well known for using a lot of server power and database space. Deactivate it and check links occasionally with an online tool instead.' ),
		'wp-statistics'          => array( 'warning', 'WP Statistics writes to the database on every visit, which slows pages and bloats the database. Google Analytics (or a lightweight privacy-friendly tool) costs your server nothing.' ),
		'wp-slimstat'            => array( 'warning', 'Slimstat writes to the database on every visit - heavy on a busy site.' ),
		'yet-another-related-posts-plugin' => array( 'warning', 'YARPP runs heavy database queries to find related posts. Use a lighter option or your theme\'s built-in related posts.' ),
		'revslider'              => array( 'warning', 'Slider Revolution is one of the heaviest plugins. In its Global Settings set "Include libraries globally" to Off, or replace the slider with a single image.' ),
		'js_composer'            => array( 'info', 'WPBakery Page Builder adds a lot of code to every page. If possible rebuild key pages with the block editor or a lighter builder.' ),
		'elementor'              => array( 'info', 'Elementor is heavy. Turn on its performance features (Elementor > Settings > Features / Performance) and only use the widgets you need.' ),
		'woocommerce'            => array( 'info', 'WooCommerce adds work to every page. Disable its scripts and "cart fragments" on non-shop pages, and keep Action Scheduler\'s queue small.' ),
		'jetpack'                => array( 'info', 'Jetpack is many plugins in one. Switch off every module you do not use (Jetpack > Settings).' ),
		'wordfence'              => array( 'info', 'Wordfence\'s firewall runs on every request and its scans are heavy on small hosting plans. If the server is slow, set scan mode to "low resource" and consider a cloud firewall (e.g. Cloudflare) instead.' ),
		'all-in-one-wp-security-and-firewall' => array( 'info', 'Security plugins run on every request. Make sure you only run one.' ),
		'nextgen-gallery'        => array( 'warning', 'NextGEN Gallery is heavy. The built-in WordPress gallery block is much lighter.' ),
		'wordpress-popular-posts' => array( 'info', 'Popular Posts tracks views in the database - turn on its caching options.' ),
		'post-views-counter'     => array( 'info', 'View counters write to the database on every visit.' ),
		'redirection'            => array( 'info', 'Redirection logs every 404 and redirect by default. Turn logging off (or keep only a few days) in Tools > Redirection > Options.' ),
		'duplicator'             => array( 'info', 'Migration plugins aren\'t needed day to day - deactivate when not in use, and delete old backup packages from the server.' ),
		'all-in-one-wp-migration' => array( 'info', 'Migration plugins aren\'t needed day to day - deactivate when not in use.' ),
		'updraftplus'            => array( 'info', 'Schedule backups for the quietest time of night - backups are hard work for the server.' ),
		'instagram-feed'         => array( 'info', 'Social feeds fetch data from other sites. Make sure caching is on in the feed settings.' ),
		'custom-facebook-feed'   => array( 'info', 'Social feeds fetch data from other sites. Make sure caching is on in the feed settings.' ),
		'essential-addons-for-elementor-lite' => array( 'info', 'Turn off every Essential Addons widget you don\'t use (Essential Addons > Elements).' ),
		'mailpoet'               => array( 'info', 'MailPoet sends email from your web server. On a busy list, use its sending service or an SMTP provider so sending doesn\'t slow the site.' ),
	);

	/** Database table name parts => plugin folder (to attribute big tables). */
	public static $tables = array(
		'actionscheduler_' => 'woocommerce / Action Scheduler',
		'wc_'              => 'woocommerce',
		'woocommerce_'     => 'woocommerce',
		'wf'               => 'wordfence',
		'statistics_'      => 'wp-statistics',
		'slim_'            => 'wp-slimstat',
		'redirection_'     => 'redirection',
		'yoast_'           => 'wordpress-seo',
		'rank_math'        => 'seo-by-rank-math',
		'aioseo_'          => 'all-in-one-seo-pack',
		'litespeed_'       => 'litespeed-cache',
		'wpmailsmtp_'      => 'wp-mail-smtp',
		'e_events'         => 'elementor',
		'e_submissions'    => 'elementor-pro',
		'wpforms_'         => 'wpforms-lite',
		'gf_'              => 'gravityforms',
		'blc_'             => 'broken-link-checker',
		'icl_'             => 'sitepress-multilingual-cms',
		'mailpoet_'        => 'mailpoet',
		'aiowps_'          => 'all-in-one-wp-security-and-firewall',
		'itsec_'           => 'better-wp-security',
		'simple_history'   => 'simple-history',
		'popularposts'     => 'wordpress-popular-posts',
		'post_views'       => 'post-views-counter',
		'wpr_'             => 'wp-rocket',
		'tec_'             => 'the-events-calendar',
		'sbi_'             => 'instagram-feed',
		'cff_'             => 'custom-facebook-feed',
		'nf3_'             => 'ninja-forms',
		'cky_'             => 'cookie-law-info',
		'wsal_'            => 'wp-security-audit-log',
		'jetpack_'         => 'jetpack',
		'nextend2_'        => 'smart-slider-3',
		'revslider_'       => 'revslider',
		'ngg_'             => 'nextgen-gallery',
		'wpda_'            => 'wp-data-access',
		'tm_'              => 'taskbuilder',
		'wpmm_'            => 'wp-maintenance-mode',
		'e_notes'          => 'elementor-pro',
	);

	/**
	 * Guess which plugin owns an option / cron hook from its name, by matching against active plugin folders.
	 *
	 * @param string $name    Option name or cron hook.
	 * @param array  $plugins Folder => plugin name.
	 */
	public static function guess_owner( $name, $plugins ) {
		$name = strtolower( ltrim( $name, '_' ) );
		$name = preg_replace( '/^(site_)?transient_(timeout_)?/', '', $name );
		$best = '';
		foreach ( $plugins as $folder => $label ) {
			$candidates = array_unique(
				array(
					strtolower( $folder ),
					str_replace( '-', '_', strtolower( $folder ) ),
					str_replace( array( '-', '_' ), '', strtolower( $folder ) ),
				)
			);
			foreach ( $candidates as $candidate ) {
				if ( strlen( $candidate ) >= 3 && 0 === strpos( $name, $candidate ) && strlen( $candidate ) > strlen( $best ) ) {
					$best = $folder;
				}
			}
		}
		if ( $best ) {
			return $best;
		}
		$prefixes = array(
			'wpseo' => 'wordpress-seo', 'yoast' => 'wordpress-seo', 'rank_math' => 'seo-by-rank-math', 'aioseo' => 'all-in-one-seo-pack',
			'woocommerce' => 'woocommerce', 'wc_' => 'woocommerce', 'elementor' => 'elementor', 'wordfence' => 'wordfence', 'wf' => 'wordfence',
			'jetpack' => 'jetpack', 'litespeed' => 'litespeed-cache', 'rocket' => 'wp-rocket', 'wp_rocket' => 'wp-rocket', 'w3tc' => 'w3-total-cache',
			'wpcf7' => 'contact-form-7', 'wpforms' => 'wpforms-lite', 'rg_gforms' => 'gravityforms', 'gform' => 'gravityforms', 'gf_' => 'gravityforms',
			'mc4wp' => 'mailchimp-for-wp', 'monsterinsights' => 'google-analytics-for-wordpress', 'googlesitekit' => 'google-site-kit',
			'updraft' => 'updraftplus', 'itsec' => 'better-wp-security', 'aiowps' => 'all-in-one-wp-security-and-firewall',
			'wpml' => 'sitepress-multilingual-cms', 'icl_' => 'sitepress-multilingual-cms', 'revslider' => 'revslider', 'rs-' => 'revslider',
			'wpb_js' => 'js_composer', 'vc_' => 'js_composer', 'et_' => 'divi', 'fusion' => 'avada', 'avada' => 'avada', 'astra' => 'astra',
			'action_scheduler' => 'woocommerce / Action Scheduler', 'as_' => 'woocommerce / Action Scheduler', 'wpo_' => 'wp-optimize',
			'sbi_' => 'instagram-feed', 'cff_' => 'custom-facebook-feed', 'mailpoet' => 'mailpoet', 'redirection' => 'redirection',
			'smush' => 'wp-smushit', 'wp-smush' => 'wp-smushit', 'imagify' => 'imagify', 'shortpixel' => 'shortpixel-image-optimiser',
			'ewww' => 'ewww-image-optimizer', 'cookie_notice' => 'cookie-notice', 'cookieyes' => 'cookie-law-info', 'complianz' => 'complianz-gdpr',
			'tribe' => 'the-events-calendar', 'tec_' => 'the-events-calendar', 'duplicator' => 'duplicator', 'ai1wm' => 'all-in-one-wp-migration',
		);
		foreach ( $prefixes as $prefix => $owner ) {
			if ( 0 === strpos( $name, $prefix ) ) {
				return $owner;
			}
		}
		return '';
	}

	public static function table_owner( $table ) {
		global $wpdb;
		$short = strtolower( 0 === strpos( $table, $wpdb->base_prefix ) ? substr( $table, strlen( $wpdb->base_prefix ) ) : $table );
		$short = preg_replace( '/^\d+_/', '', $short ); // multisite sub-site tables
		foreach ( self::$tables as $part => $owner ) {
			if ( 0 === strpos( $short, $part ) ) {
				return $owner;
			}
		}
		return '';
	}
}
