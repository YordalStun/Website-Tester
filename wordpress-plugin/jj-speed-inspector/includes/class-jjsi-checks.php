<?php
/**
 * Checks from inside WordPress that don't need a page to be loaded: the server, PHP, database, autoloaded options,
 * caching, scheduled tasks (WP-Cron) and settings.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_Checks {

	/** PHP version => date security support ended. */
	const PHP_EOL = array(
		'5.6' => '2018-12-31', '7.0' => '2019-01-10', '7.1' => '2019-12-01', '7.2' => '2020-11-30', '7.3' => '2021-12-06',
		'7.4' => '2022-11-28', '8.0' => '2023-11-26', '8.1' => '2025-12-31', '8.2' => '2026-12-31', '8.3' => '2027-12-31',
		'8.4' => '2028-12-31', '8.5' => '2029-12-31',
	);

	/** CPU benchmark time (ms) on a typical good hosting server. Slower than this = slower PHP for every page. */
	const CPU_GOOD_MS = 120;

	private static $findings = array();
	private static $data     = array();

	private static function add( $category, $severity, $title, $detail = '', $fix = '', $impact = 0, $items = array() ) {
		self::$findings[] = JJSI_Report::finding( $category, $severity, $title, $detail, $fix, $impact, $items );
	}

	public static function run() {
		self::$findings = array();
		self::$data     = array();
		$checks = array( 'php', 'benchmark', 'database_speed', 'object_cache', 'page_cache', 'autoload', 'database_size', 'transients', 'cron', 'action_scheduler', 'plugins', 'settings' );
		foreach ( $checks as $check ) {
			try {
				call_user_func( array( __CLASS__, 'check_' . $check ) );
			} catch ( Throwable $e ) {
				self::$data['errors'][] = $check . ': ' . $e->getMessage();
			}
		}
		return array( 'data' => self::$data, 'findings' => self::$findings );
	}

	/**
	 * Where active plugins and the theme call a function (e.g. flush_rewrite_rules). Returns "Name: file:line" items.
	 *
	 * @param string $needle Text to look for.
	 */
	public static function find_code( $needle, $limit = 20 ) {
		$names = self::plugin_names();
		$dirs  = array();
		foreach ( (array) get_option( 'active_plugins', array() ) as $file ) {
			$folder = dirname( $file );
			if ( '.' !== $folder && basename( JJSI_DIR ) !== $folder ) {
				$dirs[ isset( $names[ $folder ] ) ? $names[ $folder ] : $folder ] = WP_PLUGIN_DIR . '/' . $folder;
			}
		}
		$theme = wp_get_theme();
		$dirs[ $theme->get( 'Name' ) . ' (theme)' ] = $theme->get_stylesheet_directory();
		if ( $theme->parent() ) {
			$dirs[ $theme->parent()->get( 'Name' ) . ' (theme)' ] = $theme->get_template_directory();
		}
		$found   = array();
		$scanned = 0;
		foreach ( $dirs as $label => $dir ) {
			if ( ! is_dir( $dir ) ) {
				continue;
			}
			try {
				$files = new RecursiveIteratorIterator( new RecursiveDirectoryIterator( $dir, FilesystemIterator::SKIP_DOTS ) );
				foreach ( $files as $file ) {
					$path = wp_normalize_path( $file->getPathname() );
					if ( 'php' !== strtolower( $file->getExtension() ) || $file->getSize() > 1048576 || preg_match( '#/(vendor|node_modules|tests?)/#', $path ) ) {
						continue;
					}
					if ( ++$scanned > 6000 ) {
						break 2;
					}
					$code = (string) @file_get_contents( $path ); // phpcs:ignore WordPress.WP.AlternativeFunctions,WordPress.PHP.NoSilencedErrors
					$pos  = strpos( $code, $needle );
					if ( false !== $pos ) {
						$line    = substr_count( substr( $code, 0, $pos ), "\n" ) + 1;
						$found[] = $label . ': ' . str_replace( wp_normalize_path( WP_CONTENT_DIR ) . '/', '', $path ) . ':' . $line;
						if ( count( $found ) >= $limit ) {
							break 2;
						}
					}
				}
			} catch ( Exception $e ) {
				continue;
			}
		}
		return $found;
	}

	public static function bytes( $bytes ) {
		if ( $bytes >= 1073741824 ) {
			return round( $bytes / 1073741824, 1 ) . ' GB';
		}
		if ( $bytes >= 1048576 ) {
			return round( $bytes / 1048576, 1 ) . ' MB';
		}
		return max( 1, round( $bytes / 1024 ) ) . ' KB';
	}

	private static function plugin_names() {
		if ( ! function_exists( 'get_plugins' ) ) {
			require_once ABSPATH . 'wp-admin/includes/plugin.php';
		}
		$names = array();
		foreach ( get_plugins() as $file => $info ) {
			$folder           = false === strpos( $file, '/' ) ? $file : dirname( $file );
			$names[ $folder ] = $info['Name'];
		}
		return $names;
	}

	private static function owner_label( $owner, $names ) {
		if ( '' === $owner ) {
			return '';
		}
		if ( isset( $names[ $owner ] ) ) {
			return $names[ $owner ];
		}
		$theme = wp_get_theme();
		if ( in_array( $owner, array( $theme->get_stylesheet(), $theme->get_template() ), true ) || false !== strpos( $owner, ' / ' ) ) {
			return $owner;
		}
		return $owner . ' - not installed any more (left over)';
	}

	/** Scheduled tasks that belong to WordPress itself (their code only loads when cron or the admin runs). */
	const CORE_CRON = array(
		'wp_version_check', 'wp_update_plugins', 'wp_update_themes', 'wp_scheduled_delete', 'wp_scheduled_auto_draft_delete',
		'delete_expired_transients', 'wp_privacy_delete_old_export_files', 'wp_site_health_scheduled_check',
		'recovery_mode_clean_expired_keys', 'wp_delete_temp_updater_backups', 'wp_https_detection', 'wp_update_user_counts',
		'wp_maybe_auto_update', 'wp_update_comment_type_batch', 'importer_scheduled_cleanup', 'upgrader_scheduled_cleanup',
		'wp_split_shared_term_batch', 'do_pings', 'publish_future_post', 'wp_batch_split_terms',
	);

	/** Built-in post types - anything else with tens of thousands of entries is worth naming. */
	const CORE_POST_TYPES = array( 'post', 'page', 'attachment', 'revision', 'nav_menu_item', 'custom_css', 'customize_changeset', 'oembed_cache', 'user_request', 'wp_block', 'wp_template', 'wp_template_part', 'wp_global_styles', 'wp_navigation', 'wp_font_family', 'wp_font_face' );

	/** Post types plugins use to store submissions, logs and so on. */
	const POST_TYPE_OWNERS = array(
		'wpcf7s' => 'Contact Form Submissions (saved form entries, including spam)', 'flamingo_inbound' => 'Flamingo (saved form entries)',
		'flamingo_contact' => 'Flamingo', 'shop_order' => 'WooCommerce orders', 'shop_order_placehold' => 'WooCommerce orders',
		'scheduled-action' => 'Action Scheduler (old background jobs)', 'wpforms_log' => 'WPForms logs', 'mc4wp-form' => 'Mailchimp for WP',
		'ozy_project' => 'BuildMe theme projects', 'product' => 'WooCommerce products', 'product_variation' => 'WooCommerce product variations',
		'elementor_library' => 'Elementor templates', 'wpcf7_contact_form' => 'Contact Form 7 forms', 'nf_sub' => 'Ninja Forms submissions',
		'cfdb7' => 'Contact Form 7 Database', 'jetpack_feedback' => 'Jetpack form responses', 'feedback' => 'Jetpack form responses',
		'ml-slider' => 'MetaSlider', 'acf-field' => 'Advanced Custom Fields', 'wp_log' => 'a logging plugin', 'cs_log' => 'a logging plugin',
	);

	// ----------------------------------------------------------------------------------------------------
	// Server and PHP
	// ----------------------------------------------------------------------------------------------------

	private static function check_php() {
		$version = PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION;
		self::$data['php'] = array(
			'version'        => PHP_VERSION,
			'sapi'           => PHP_SAPI,
			'memory_limit'   => ini_get( 'memory_limit' ),
			'wp_memory'      => defined( 'WP_MEMORY_LIMIT' ) ? WP_MEMORY_LIMIT : '',
			'max_execution'  => ini_get( 'max_execution_time' ),
			'server'         => isset( $_SERVER['SERVER_SOFTWARE'] ) ? sanitize_text_field( wp_unslash( $_SERVER['SERVER_SOFTWARE'] ) ) : '',
			'os'             => PHP_OS,
		);
		$eol = isset( self::PHP_EOL[ $version ] ) ? self::PHP_EOL[ $version ] : null;
		if ( version_compare( $version, '7.4', '<' ) || ( $eol && strtotime( $eol ) < time() ) ) {
			self::add(
				'server', 'critical', sprintf( 'Old, unsupported PHP version (%s)', PHP_VERSION ),
				sprintf( 'PHP %s stopped getting security fixes%s. Newer PHP is also much faster: PHP 8.x typically runs WordPress noticeably quicker than 7.x.', $version, $eol ? ' on ' . date_i18n( 'j F Y', strtotime( $eol ) ) : '' ),
				'Update plugins and your theme first, then change the PHP version in your hosting control panel (cPanel: "Select PHP Version" or "MultiPHP Manager") to 8.3 or newer. Check the site afterwards.',
				300
			);
		} elseif ( $eol && strtotime( $eol ) < time() + 180 * DAY_IN_SECONDS ) {
			self::add( 'server', 'warning', sprintf( 'PHP %s stops getting security fixes soon (%s)', $version, date_i18n( 'F Y', strtotime( $eol ) ) ), '', 'Plan a move to PHP 8.3 or newer.' );
		} else {
			self::add( 'server', 'good', sprintf( 'PHP %s is a supported version', PHP_VERSION ) );
		}

		$limit = wp_convert_hr_to_bytes( ini_get( 'memory_limit' ) );
		if ( $limit > 0 && $limit < 128 * 1048576 ) {
			self::add( 'server', 'warning', 'PHP memory limit is low (' . ini_get( 'memory_limit' ) . ')', 'Heavier plugins (page builders, WooCommerce) can run out of memory, causing errors or blank pages.', 'Raise memory_limit to at least 256M in your hosting control panel (or ask your host).' );
		}

		// OPcache keeps compiled PHP in memory - without it PHP re-reads and re-compiles every file on every request.
		$opcache = function_exists( 'opcache_get_status' ) ? @opcache_get_status( false ) : false; // phpcs:ignore WordPress.PHP.NoSilencedErrors
		if ( ! $opcache || empty( $opcache['opcache_enabled'] ) ) {
			if ( 'cli' !== PHP_SAPI ) {
				self::add( 'server', 'critical', 'PHP OPcache is switched off', 'Without OPcache, PHP has to read and compile all of WordPress and every plugin again for every single page. Turning it on typically makes uncached pages 2-3 times faster.', 'Enable the OPcache extension in your hosting control panel (cPanel: "Select PHP Version" > Extensions > opcache) or ask your host.', 600 );
			}
			self::$data['opcache'] = false;
		} else {
			$mem    = isset( $opcache['memory_usage'] ) ? $opcache['memory_usage'] : array();
			$stats  = isset( $opcache['opcache_statistics'] ) ? $opcache['opcache_statistics'] : array();
			$free   = isset( $mem['free_memory'] ) ? $mem['free_memory'] : 0;
			$used   = isset( $mem['used_memory'] ) ? $mem['used_memory'] : 0;
			$full   = ! empty( $opcache['cache_full'] ) || ( $used + $free > 0 && $free / ( $used + $free ) < 0.05 );
			self::$data['opcache'] = array(
				'memory_mb' => round( ( $used + $free ) / 1048576 ),
				'hit_rate'  => isset( $stats['opcache_hit_rate'] ) ? round( $stats['opcache_hit_rate'], 1 ) : null,
				'full'      => $full,
			);
			if ( $full ) {
				self::add( 'server', 'warning', 'PHP OPcache is full', 'OPcache has run out of memory, so some PHP files are being recompiled on every request.', 'Ask your host to increase opcache.memory_consumption (256 MB is a good size for WordPress with many plugins).', 150 );
			} else {
				self::add( 'server', 'good', 'PHP OPcache is on' );
			}
		}
	}

	/** A fixed amount of typical PHP work: string handling, arrays, hashing, maths. */
	public static function cpu_benchmark() {
		$best = PHP_INT_MAX;
		for ( $round = 0; $round < 3; $round++ ) {
			$start = hrtime( true );
			$out   = '';
			$list  = array();
			for ( $i = 0; $i < 60000; $i++ ) {
				$s      = md5( 'jjsi' . $i );
				$list[] = strtoupper( substr( $s, 0, 8 ) ) . str_repeat( 'x', $i % 7 );
				$out   .= sprintf( '%05d', $i % 1000 );
				$x      = sqrt( $i ) * sin( $i ) + intdiv( $i, 3 );
			}
			sort( $list );
			$joined = implode( ',', $list );
			$parts  = explode( ',', $joined );
			$json   = json_encode( array_slice( $parts, 0, 5000 ) );
			json_decode( $json, true );
			preg_match_all( '/[A-F]{3}/', $joined, $m );
			$best = min( $best, ( hrtime( true ) - $start ) / 1e6 );
		}
		return round( $best, 1 );
	}

	private static function check_benchmark() {
		$ms    = self::cpu_benchmark();
		$ratio = $ms / self::CPU_GOOD_MS;
		self::$data['cpu_ms'] = $ms;
		self::$data['cpu_ratio'] = round( $ratio, 2 );
		if ( $ratio > 3.5 ) {
			self::add( 'server', 'critical', sprintf( 'The server\'s processor is slow (%.1fx slower than a typical good host)', $ratio ),
				sprintf( 'A fixed PHP test took %d ms (a good WordPress host does it in about %d ms). Everything WordPress does takes about that much longer - this is the hosting itself, not a plugin. Cheap shared hosting plans often share one processor between many busy sites.', $ms, self::CPU_GOOD_MS ),
				'Upgrade to a better hosting plan or move to a faster host. Run this test at different times of day: if it varies a lot, the server is overloaded by other sites.', (int) ( ( $ratio - 1 ) * 200 ) );
		} elseif ( $ratio > 2 ) {
			self::add( 'server', 'warning', sprintf( 'The server\'s processor is on the slow side (%.1fx slower than a typical good host)', $ratio ),
				sprintf( 'A fixed PHP test took %d ms (a good WordPress host takes about %d ms).', $ms, self::CPU_GOOD_MS ),
				'If pages are slow even after fixing plugins and caching, the hosting plan is the next thing to upgrade.', (int) ( ( $ratio - 1 ) * 120 ) );
		} else {
			self::add( 'server', 'good', sprintf( 'Server processor speed is good (PHP test: %d ms)', $ms ) );
		}
	}

	private static function check_database_speed() {
		global $wpdb;
		$times = array();
		for ( $i = 0; $i < 60; $i++ ) {
			$start = hrtime( true );
			$wpdb->get_var( "SELECT option_value FROM {$wpdb->options} WHERE option_name = 'siteurl'" ); // phpcs:ignore WordPress.DB.DirectDatabaseQuery
			$times[] = ( hrtime( true ) - $start ) / 1e6;
		}
		sort( $times );
		$median = $times[ (int) ( count( $times ) / 2 ) ];
		self::$data['db_query_ms'] = round( $median, 3 );
		self::$data['db_version']  = $wpdb->db_version();
		self::$data['db_server']   = method_exists( $wpdb, 'db_server_info' ) ? $wpdb->db_server_info() : '';
		self::$data['db_host']     = DB_HOST;
		if ( $median > 1.5 ) {
			self::add( 'database', 'critical', sprintf( 'The database is slow to answer (%.1f ms per simple query)', $median ),
				'Even the simplest database question takes this long. A typical page asks 30-200 questions, so this adds up fast. Usually the database server is overloaded or on a different, distant machine.',
				'Ask your host about database performance (and whether the database server is in the same data centre). Adding an object cache (Redis) reduces how often WordPress needs to ask.', (int) ( $median * 100 ) );
		} elseif ( $median > 0.6 ) {
			self::add( 'database', 'warning', sprintf( 'The database is a little slow (%.1f ms per simple query)', $median ),
				'A healthy database on the same server answers simple questions in well under half a millisecond.', 'An object cache (Redis) reduces database work; ask your host whether the database is on a separate server.', (int) ( $median * 60 ) );
		} else {
			self::add( 'database', 'good', sprintf( 'The database answers quickly (%.2f ms per simple query)', $median ) );
		}
		$server = strtolower( self::$data['db_server'] );
		$ver    = self::$data['db_version'];
		if ( preg_match( '/(\d+\.\d+(?:\.\d+)?)-mariadb/', $server, $m ) ) {
			$ver = $m[1]; // older PHP reports MariaDB as "5.5.5-10.x.y-MariaDB"
			self::$data['db_version'] = $ver;
		}
		if ( false !== strpos( $server, 'mariadb' ) ? version_compare( $ver, '10.4', '<' ) : version_compare( $ver, '5.7', '<' ) ) {
			self::add( 'database', 'warning', 'Old database server version (' . ( self::$data['db_server'] ? self::$data['db_server'] : $ver ) . ')', 'Newer MySQL/MariaDB versions are faster and still get security fixes.', 'Ask your host to upgrade to MySQL 8 or MariaDB 10.6+.' );
		}
	}

	private static function check_object_cache() {
		$external = wp_using_ext_object_cache();
		$redis    = class_exists( 'Redis' ) || extension_loaded( 'redis' );
		$memcache = class_exists( 'Memcached' ) || class_exists( 'Memcache' );
		self::$data['object_cache'] = array( 'enabled' => $external, 'redis_available' => $redis, 'memcached_available' => $memcache, 'dropin' => file_exists( WP_CONTENT_DIR . '/object-cache.php' ) );
		if ( $external ) {
			self::add( 'caching', 'good', 'A persistent object cache is in use', 'Database results are kept in memory between page loads.' );
		} elseif ( $redis || $memcache ) {
			self::add( 'caching', 'warning', 'Redis/Memcached is available but WordPress isn\'t using it',
				'Your server already has ' . ( $redis ? 'Redis' : 'Memcached' ) . ' support. An object cache stores database results in memory so pages that can\'t be cached (logged-in users, carts, admin) need far fewer database queries.',
				$redis ? 'Install the free "Redis Object Cache" plugin and click "Enable Object Cache" (or turn on the object cache in LiteSpeed Cache / W3 Total Cache, or your host\'s control panel).' : 'Turn on the object cache in your caching plugin or ask your host.', 150 );
		} else {
			self::add( 'caching', 'info', 'No persistent object cache',
				'An object cache (Redis or Memcached) keeps database results in memory, which mostly helps logged-in users, shops and the admin area.',
				'Ask your host whether they offer Redis - many good hosts include it.' );
		}
	}

	private static function check_page_cache() {
		$names   = self::plugin_names();
		$active  = (array) get_option( 'active_plugins', array() );
		$caching = array();
		foreach ( $active as $file ) {
			$folder = dirname( $file );
			if ( isset( JJSI_Known::$cache_plugins[ $folder ] ) ) {
				$caching[] = JJSI_Known::$cache_plugins[ $folder ];
			}
		}
		$dropin   = file_exists( WP_CONTENT_DIR . '/advanced-cache.php' );
		$wp_cache = defined( 'WP_CACHE' ) && WP_CACHE;
		// advanced-cache.php only runs when WP_CACHE is true in wp-config.php
		self::$data['page_cache'] = array( 'plugins' => $caching, 'advanced_cache' => $dropin, 'wp_cache' => $wp_cache, 'active' => $caching || ( $dropin && $wp_cache ) );
		if ( $dropin && ! $wp_cache ) {
			self::add( 'caching', 'info', 'A leftover page-cache file does nothing',
				'wp-content/advanced-cache.php exists, but WP_CACHE is off in wp-config.php, so it never runs. It was most likely left behind by a caching plugin that has been removed.',
				'When you install a caching plugin it will replace this file. Until then it can be deleted (back up first).' );
		}
		if ( count( $caching ) > 1 ) {
			self::add( 'caching', 'warning', count( $caching ) . ' caching plugins are active at once',
				'Caching plugins fight each other: double processing, broken pages and caches that never clear properly.',
				'Keep one (usually the one your host recommends - e.g. LiteSpeed Cache on LiteSpeed servers) and deactivate the others.', 100, $caching );
		}
		unset( $names );
	}

	// ----------------------------------------------------------------------------------------------------
	// Database contents
	// ----------------------------------------------------------------------------------------------------

	private static function autoload_values() {
		return function_exists( 'wp_autoload_values_to_autoload' ) ? wp_autoload_values_to_autoload() : array( 'yes' );
	}

	private static function check_autoload() {
		global $wpdb;
		$values = self::autoload_values();
		$in     = implode( ',', array_fill( 0, count( $values ), '%s' ) );
		// phpcs:disable WordPress.DB.PreparedSQLPlaceholders,WordPress.DB.DirectDatabaseQuery,WordPress.DB.PreparedSQL
		$total = (int) $wpdb->get_var( $wpdb->prepare( "SELECT SUM(LENGTH(option_value)) FROM {$wpdb->options} WHERE autoload IN ($in)", $values ) );
		$count = (int) $wpdb->get_var( $wpdb->prepare( "SELECT COUNT(*) FROM {$wpdb->options} WHERE autoload IN ($in)", $values ) );
		$top   = $wpdb->get_results( $wpdb->prepare( "SELECT option_name, LENGTH(option_value) AS size FROM {$wpdb->options} WHERE autoload IN ($in) ORDER BY size DESC LIMIT 15", $values ), ARRAY_A );
		// phpcs:enable
		$names = self::plugin_names();
		$rows  = array();
		foreach ( $top as $row ) {
			$owner  = JJSI_Known::guess_owner( $row['option_name'], $names );
			$rows[] = array( 'name' => $row['option_name'], 'size' => (int) $row['size'], 'owner' => self::owner_label( $owner, $names ) );
		}
		self::$data['autoload'] = array( 'bytes' => $total, 'count' => $count, 'top' => $rows );
		$items = array();
		foreach ( array_slice( $rows, 0, 10 ) as $row ) {
			if ( $row['size'] > 20 * 1024 ) {
				$items[] = $row['name'] . ' - ' . self::bytes( $row['size'] ) . ( $row['owner'] ? ' (' . $row['owner'] . ')' : '' );
			}
		}
		$text = sprintf( 'WordPress loads %s of settings ("autoloaded options", %d of them) from the database on every single page, before anything else happens.', self::bytes( $total ), $count );
		$fix  = 'Find which plugins the biggest options belong to (listed below). Options left behind by deleted plugins can be removed with WP-Optimize or Advanced Database Cleaner (back up first). Some plugins store logs or caches in options - check their settings.';
		if ( $total > 2 * 1048576 ) {
			self::add( 'database', 'critical', 'Far too much data is loaded on every page (' . self::bytes( $total ) . ' of autoloaded options)', $text . ' WordPress recommends under 800 KB.', $fix, (int) ( $total / 1048576 * 100 ), $items );
		} elseif ( $total > 800 * 1024 ) {
			self::add( 'database', 'warning', 'Lots of data is loaded on every page (' . self::bytes( $total ) . ' of autoloaded options)', $text . ' WordPress recommends under 800 KB.', $fix, (int) ( $total / 1048576 * 80 ), $items );
		} else {
			self::add( 'database', 'good', 'Autoloaded options are a healthy size (' . self::bytes( $total ) . ')' );
		}
	}

	private static function check_database_size() {
		global $wpdb;
		// phpcs:disable WordPress.DB.DirectDatabaseQuery,WordPress.DB.PreparedSQL
		$tables = $wpdb->get_results(
			$wpdb->prepare(
				'SELECT TABLE_NAME AS name, ENGINE AS engine, TABLE_ROWS AS row_count, DATA_LENGTH + INDEX_LENGTH AS size, DATA_FREE AS free FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME LIKE %s ORDER BY size DESC',
				DB_NAME,
				$wpdb->esc_like( $wpdb->base_prefix ) . '%'
			),
			ARRAY_A
		);
		$names = self::plugin_names();
		$total = 0;
		$myisam = array();
		$rows  = array();
		foreach ( (array) $tables as $t ) {
			$total += (int) $t['size'];
			if ( 'MyISAM' === $t['engine'] ) {
				$myisam[] = $t['name'];
			}
			if ( count( $rows ) < 12 ) {
				$owner  = JJSI_Known::table_owner( $t['name'] );
				$rows[] = array( 'name' => $t['name'], 'size' => (int) $t['size'], 'rows' => (int) $t['row_count'], 'engine' => $t['engine'], 'owner' => self::owner_label( $owner, $names ) );
			}
		}
		$counts = array(
			'revisions'      => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_type = 'revision'" ),
			'auto_drafts'    => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_status = 'auto-draft'" ),
			'trash_posts'    => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_status = 'trash'" ),
			'spam_comments'  => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->comments} WHERE comment_approved IN ('spam','trash')" ),
			'posts'          => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->posts}" ),
			'postmeta'       => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->postmeta}" ),
			'options'        => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->options}" ),
		);
		if ( $counts['postmeta'] < 3000000 ) {
			$counts['orphan_postmeta'] = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->postmeta} pm LEFT JOIN {$wpdb->posts} p ON p.ID = pm.post_id WHERE p.ID IS NULL" );
		}
		$post_types = array();
		if ( $counts['posts'] > 20000 ) {
			foreach ( (array) $wpdb->get_results( "SELECT post_type, COUNT(*) AS n FROM {$wpdb->posts} GROUP BY post_type ORDER BY n DESC LIMIT 10", ARRAY_A ) as $row ) {
				$post_types[ $row['post_type'] ] = (int) $row['n'];
			}
		}
		$meta_keys = array();
		if ( $counts['postmeta'] > 300000 && $counts['postmeta'] < 20000000 ) {
			foreach ( (array) $wpdb->get_results( "SELECT meta_key, COUNT(*) AS n FROM {$wpdb->postmeta} GROUP BY meta_key ORDER BY n DESC LIMIT 12", ARRAY_A ) as $row ) {
				$meta_keys[ (string) $row['meta_key'] ] = (int) $row['n'];
			}
		}
		// phpcs:enable
		self::$data['database'] = array( 'bytes' => $total, 'tables' => $rows, 'table_count' => count( (array) $tables ), 'myisam' => $myisam, 'counts' => $counts, 'post_types' => $post_types, 'meta_keys' => $meta_keys );
		foreach ( $post_types as $type => $n ) {
			if ( $n < 10000 || in_array( $type, self::CORE_POST_TYPES, true ) ) {
				continue;
			}
			$owner = isset( self::POST_TYPE_OWNERS[ $type ] ) ? self::POST_TYPE_OWNERS[ $type ] : self::owner_label( JJSI_Known::guess_owner( $type, $names ), $names );
			self::add( 'database', $n > 50000 ? 'warning' : 'info',
				sprintf( '%s stored "%s" items fill the posts table%s', number_format_i18n( $n ), $type, $owner ? ' (' . $owner . ')' : '' ),
				sprintf( 'That\'s %d%% of everything in wp_posts, plus their rows in wp_postmeta. Old form entries and logs pile up for years and slow down queries and backups.', round( $n * 100 / max( 1, $counts['posts'] ) ) ),
				'If these are old submissions or logs you don\'t need, export what you want to keep, then delete the old ones (or set the plugin to keep fewer). Back up first.',
				(int) min( 200, $n / 1000 ) );
		}

		foreach ( $rows as $t ) {
			if ( $t['owner'] && false !== strpos( $t['owner'], 'left over' ) && $t['size'] > 1048576 ) {
				self::add( 'database', 'info', sprintf( 'Table %s is left over from a removed plugin (%s)', $t['name'], self::bytes( $t['size'] ) ),
					'The plugin that created it is no longer installed, so the table is just taking up space and making backups bigger.',
					'After a backup, delete the table (e.g. with phpMyAdmin or Advanced Database Cleaner).' );
			}
			if ( $t['size'] > 200 * 1048576 || ( $t['owner'] && $t['size'] > 50 * 1048576 ) ) {
				$items = array();
				$what  = $wpdb->postmeta === $t['name'] ? $meta_keys : ( $wpdb->posts === $t['name'] ? $post_types : array() );
				foreach ( $what as $key => $n ) {
					$items[] = sprintf( '%s: %s rows', '' === $key ? '(empty)' : $key, number_format_i18n( $n ) );
				}
				self::add( 'database', $t['size'] > 500 * 1048576 ? 'critical' : 'warning',
					sprintf( 'Huge database table: %s (%s%s)', $t['name'], self::bytes( $t['size'] ), $t['owner'] ? ', from ' . $t['owner'] : '' ),
					sprintf( 'This table has about %s rows. Huge tables slow down every query that uses them, and backups.', number_format_i18n( $t['rows'] ) ),
					$t['owner'] ? 'Check ' . $t['owner'] . '\'s settings for log/history retention and clear old entries. If the plugin is no longer used, delete the plugin and its table.' : ( $items ? 'The details below show what fills it - the biggest entries usually point to the plugin responsible (e.g. saved form submissions or logs).' : 'Find which plugin created this table and clean up its old data.' ),
					(int) ( $t['size'] / 1048576 / 2 ), $items );
			}
		}
		if ( $myisam ) {
			self::add( 'database', 'warning', count( $myisam ) . ' database tables use the old MyISAM engine',
				'MyISAM locks a whole table while writing, so a busy site (comments, orders, logs) makes visitors wait. InnoDB is faster and safer.',
				'Convert them to InnoDB (back up first) - most hosts can do it for you, or use a plugin such as "Index WP MySQL For Speed".', 80, array_slice( $myisam, 0, 15 ) );
		}
		$junk = array();
		if ( $counts['revisions'] > 1000 ) {
			$junk[] = number_format_i18n( $counts['revisions'] ) . ' post revisions';
		}
		if ( $counts['auto_drafts'] > 200 ) {
			$junk[] = number_format_i18n( $counts['auto_drafts'] ) . ' auto-drafts';
		}
		if ( $counts['trash_posts'] > 200 ) {
			$junk[] = number_format_i18n( $counts['trash_posts'] ) . ' posts in the trash';
		}
		if ( $counts['spam_comments'] > 1000 ) {
			$junk[] = number_format_i18n( $counts['spam_comments'] ) . ' spam/trashed comments';
		}
		if ( ! empty( $counts['orphan_postmeta'] ) && $counts['orphan_postmeta'] > 1000 ) {
			$junk[] = number_format_i18n( $counts['orphan_postmeta'] ) . ' orphaned post meta rows (left behind by deleted posts)';
		}
		if ( $junk ) {
			self::add( 'database', 'info', 'The database could do with a clean-up',
				'Old revisions, drafts, spam and leftovers make the database bigger and some queries slower.',
				'Take a backup, then clean up with WP-Optimize (free). Limit revisions by adding define( \'WP_POST_REVISIONS\', 10 ); to wp-config.php.', 30, $junk );
		}
	}

	private static function check_transients() {
		global $wpdb;
		if ( wp_using_ext_object_cache() ) {
			return; // transients live in the object cache, not the database
		}
		// phpcs:disable WordPress.DB.DirectDatabaseQuery
		$expired = (int) $wpdb->get_var( $wpdb->prepare( "SELECT COUNT(*) FROM {$wpdb->options} WHERE option_name LIKE %s AND option_value < %d", $wpdb->esc_like( '_transient_timeout_' ) . '%', time() ) );
		$total   = (int) $wpdb->get_var( $wpdb->prepare( "SELECT COUNT(*) FROM {$wpdb->options} WHERE option_name LIKE %s", $wpdb->esc_like( '_transient_' ) . '%' ) );
		// phpcs:enable
		self::$data['transients'] = array( 'total' => $total, 'expired' => $expired );
		if ( $expired > 1000 ) {
			self::add( 'database', 'warning', number_format_i18n( $expired ) . ' expired transients are piling up',
				'Transients are temporary cached data. Expired ones should be cleaned automatically; this many means the clean-up isn\'t keeping up (often because WP-Cron isn\'t running).',
				'Delete expired transients with WP-Optimize or Transients Manager, and check the scheduled tasks section.', 40 );
		}
	}

	// ----------------------------------------------------------------------------------------------------
	// Scheduled tasks
	// ----------------------------------------------------------------------------------------------------

	private static function check_cron() {
		$crons = function_exists( '_get_cron_array' ) ? _get_cron_array() : array();
		$now   = time();
		$names = self::plugin_names();
		$active_folders = array_map( 'dirname', (array) get_option( 'active_plugins', array() ) );
		$total = 0;
		$overdue = array();
		$frequent = array();
		$orphans = array();
		$per_hook = array();
		foreach ( (array) $crons as $timestamp => $hooks ) {
			foreach ( (array) $hooks as $hook => $events ) {
				foreach ( (array) $events as $event ) {
					$total++;
					$per_hook[ $hook ] = ( isset( $per_hook[ $hook ] ) ? $per_hook[ $hook ] : 0 ) + 1;
					if ( $timestamp < $now - 15 * MINUTE_IN_SECONDS ) {
						$overdue[ $hook ] = max( isset( $overdue[ $hook ] ) ? $overdue[ $hook ] : 0, $now - $timestamp );
					}
					if ( ! empty( $event['interval'] ) && $event['interval'] < 5 * MINUTE_IN_SECONDS ) {
						$frequent[ $hook ] = $event['interval'];
					}
					if ( ! has_action( $hook ) && ! in_array( $hook, self::CORE_CRON, true ) ) {
						$owner = JJSI_Known::guess_owner( $hook, $names );
						// Many plugins only attach their task code when cron itself runs, so only count tasks whose
						// plugin isn't active.
						if ( '' === $owner || ! in_array( $owner, $active_folders, true ) ) {
							$orphans[ $hook ] = true;
						}
					}
				}
			}
		}
		$disabled = defined( 'DISABLE_WP_CRON' ) && DISABLE_WP_CRON;
		self::$data['cron'] = array( 'events' => $total, 'overdue' => $overdue, 'disabled' => $disabled, 'frequent' => $frequent, 'orphans' => array_keys( $orphans ) );
		$label = function ( $hook ) use ( $names ) {
			$owner = JJSI_Known::guess_owner( $hook, $names );
			return $hook . ( $owner ? ' (' . ( isset( $names[ $owner ] ) ? $names[ $owner ] : $owner ) . ')' : '' );
		};
		if ( $overdue ) {
			arsort( $overdue );
			$worst = reset( $overdue );
			$items = array();
			foreach ( array_slice( $overdue, 0, 12, true ) as $hook => $late ) {
				$items[] = $label( $hook ) . ' - ' . human_time_diff( $now - $late, $now ) . ' late';
			}
			$severity = $worst > DAY_IN_SECONDS ? 'critical' : 'warning';
			self::add( 'cron', $severity, count( $overdue ) . ' scheduled tasks are overdue (WP-Cron isn\'t keeping up)',
				'WordPress runs scheduled jobs (backups, emails, cleanup, publishing scheduled posts) when visitors load pages. '
				. ( $disabled ? 'WP-Cron is switched off in wp-config.php (DISABLE_WP_CRON), and it looks like no real server cron job is running it instead.' : 'These jobs are running late, so either the site gets few visits or the jobs are failing/too slow.' ),
				$disabled ? 'Set up a real cron job in your hosting control panel that runs every 5 minutes: wget -q -O - ' . site_url( 'wp-cron.php?doing_wp_cron' ) . ' >/dev/null 2>&1' : 'Best practice: add define( \'DISABLE_WP_CRON\', true ); to wp-config.php and create a real cron job in your hosting control panel that loads ' . site_url( 'wp-cron.php' ) . ' every 5 minutes.',
				$severity === 'critical' ? 150 : 60, $items );
		} else {
			self::add( 'cron', 'good', 'Scheduled tasks are running on time', sprintf( '%d scheduled tasks.', $total ) );
		}
		if ( $frequent ) {
			$items = array();
			foreach ( $frequent as $hook => $interval ) {
				$items[] = $label( $hook ) . ' - every ' . human_time_diff( 0, $interval );
			}
			self::add( 'cron', 'info', count( $frequent ) . ' tasks run every few minutes', 'Very frequent tasks keep the server busy. When WP-Cron runs from page visits, one unlucky visitor waits for them.', 'Check these plugins\' settings, and use a real server cron job instead of page-visit cron.', 30, $items );
		}
		if ( count( $orphans ) > 3 ) {
			$items = array_map( $label, array_slice( array_keys( $orphans ), 0, 15 ) );
			self::add( 'cron', 'info', count( $orphans ) . ' scheduled tasks probably belong to removed plugins',
				'No active plugin seems to handle these tasks - they\'re usually left behind by plugins you\'ve deleted. They\'re harmless but clutter the schedule.',
				'Remove them with the free WP Crontrol plugin (Tools > Cron Events).', 0, $items );
		}
	}

	private static function check_action_scheduler() {
		global $wpdb;
		$table = $wpdb->prefix . 'actionscheduler_actions';
		if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) ) !== $table ) { // phpcs:ignore WordPress.DB.DirectDatabaseQuery
			return;
		}
		// phpcs:disable WordPress.DB.DirectDatabaseQuery,WordPress.DB.PreparedSQL.InterpolatedNotPrepared
		$past_due = (int) $wpdb->get_var( $wpdb->prepare( "SELECT COUNT(*) FROM {$table} WHERE status = 'pending' AND scheduled_date_gmt < %s", gmdate( 'Y-m-d H:i:s', time() - HOUR_IN_SECONDS ) ) );
		$failed   = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$table} WHERE status = 'failed'" );
		$complete = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$table} WHERE status = 'complete'" );
		// phpcs:enable
		self::$data['action_scheduler'] = array( 'past_due' => $past_due, 'failed' => $failed, 'complete' => $complete );
		if ( $past_due > 100 ) {
			self::add( 'cron', 'warning', number_format_i18n( $past_due ) . ' background jobs are stuck (Action Scheduler)',
				'Action Scheduler (used by WooCommerce and many other plugins) has a backlog of jobs that should already have run - things like emails, stock syncs and webhooks.',
				'Look at Tools > Scheduled Actions > Past-due to see which plugin they belong to. A real server cron job helps the queue keep up.', 80 );
		}
		if ( $complete > 50000 || $failed > 5000 ) {
			self::add( 'database', 'info', 'Action Scheduler keeps a lot of old job history',
				sprintf( '%s completed and %s failed jobs are stored.', number_format_i18n( $complete ), number_format_i18n( $failed ) ),
				'Old entries are normally cleaned after 30 days. If the queue is huge, clear completed/failed actions in Tools > Scheduled Actions.' );
		}
	}

	// ----------------------------------------------------------------------------------------------------
	// Plugins, theme and settings
	// ----------------------------------------------------------------------------------------------------

	private static function check_plugins() {
		$names   = self::plugin_names();
		$active  = (array) get_option( 'active_plugins', array() );
		$updates = get_site_transient( 'update_plugins' );
		$outdated = array();
		foreach ( $active as $file ) {
			if ( isset( $updates->response[ $file ] ) ) {
				$folder     = dirname( $file );
				$outdated[] = ( isset( $names[ $folder ] ) ? $names[ $folder ] : $folder ) . ' -> ' . $updates->response[ $file ]->new_version;
			}
		}
		$all = count( $names );
		self::$data['plugins'] = array( 'active' => count( $active ), 'installed' => $all, 'outdated' => $outdated );
		if ( $outdated ) {
			self::add( 'plugins', count( $outdated ) > 3 ? 'critical' : 'warning', count( $outdated ) . ' active plugins need updating',
				'Updates fix security holes and often speed problems.', 'Back up, then update them in Dashboard > Updates.', 50, $outdated );
		}
		if ( count( $active ) > 40 ) {
			self::add( 'plugins', 'warning', count( $active ) . ' active plugins', 'Every active plugin\'s code runs on every page that isn\'t served from the cache. The plugin timings in this report show which ones cost the most.', 'Deactivate and delete plugins you don\'t really need.', 50 );
		}
		if ( $all - count( $active ) > 10 ) {
			self::add( 'plugins', 'info', ( $all - count( $active ) ) . ' plugins are installed but not active', 'Inactive plugins don\'t slow the site, but they can still be a security risk if they\'re outdated.', 'Delete the ones you don\'t need.' );
		}
		foreach ( $active as $file ) {
			$folder = dirname( $file );
			if ( isset( JJSI_Known::$advice[ $folder ] ) && 'warning' === JJSI_Known::$advice[ $folder ][0] ) {
				self::add( 'plugins', 'warning', ( isset( $names[ $folder ] ) ? $names[ $folder ] : $folder ) . ' slows the site down', JJSI_Known::$advice[ $folder ][1], '', 100 );
			}
		}
		$theme = wp_get_theme();
		$theme_updates = get_site_transient( 'update_themes' );
		self::$data['theme'] = array( 'name' => $theme->get( 'Name' ), 'version' => $theme->get( 'Version' ), 'parent' => $theme->parent() ? $theme->parent()->get( 'Name' ) : '' );
		foreach ( array_filter( array( $theme->get_stylesheet(), $theme->get_template() ) ) as $slug ) {
			if ( isset( $theme_updates->response[ $slug ] ) ) {
				self::add( 'plugins', 'warning', 'Theme "' . wp_get_theme( $slug )->get( 'Name' ) . '" needs updating', '', 'Back up, then update it in Dashboard > Updates.' );
			}
		}
		$core = get_site_transient( 'update_core' );
		if ( ! empty( $core->updates ) && 'upgrade' === $core->updates[0]->response ) {
			self::add( 'settings', 'warning', 'WordPress needs updating (' . get_bloginfo( 'version' ) . ' -> ' . $core->updates[0]->current . ')', 'Each WordPress release brings security and speed fixes.', 'Back up, then update in Dashboard > Updates.' );
		}
	}

	private static function check_settings() {
		$debug = array();
		if ( defined( 'WP_DEBUG' ) && WP_DEBUG ) {
			$debug[] = 'WP_DEBUG is on';
			if ( ! defined( 'WP_DEBUG_DISPLAY' ) || WP_DEBUG_DISPLAY ) {
				$debug[] = 'errors are shown to visitors (WP_DEBUG_DISPLAY)';
			}
		}
		if ( defined( 'SAVEQUERIES' ) && SAVEQUERIES && ! JJSI_Profiler::$active ) {
			$debug[] = 'SAVEQUERIES is on (stores every database query in memory)';
		}
		if ( defined( 'SCRIPT_DEBUG' ) && SCRIPT_DEBUG ) {
			$debug[] = 'SCRIPT_DEBUG is on (loads the big, unminified versions of WordPress\'s scripts)';
		}
		if ( $debug ) {
			self::add( 'settings', 'warning', 'Debugging is switched on', 'Debug settings are for developers - on a live site they slow things down and can show error details to visitors.', 'Set these to false in wp-config.php: WP_DEBUG, SAVEQUERIES, SCRIPT_DEBUG.', 80, $debug );
		}
		$log = WP_CONTENT_DIR . '/debug.log';
		if ( defined( 'WP_DEBUG_LOG' ) && is_string( WP_DEBUG_LOG ) ) {
			$log = WP_DEBUG_LOG;
		}
		if ( @is_file( $log ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors
			$size = (int) @filesize( $log ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
			self::$data['debug_log'] = $size;
			if ( $size > 50 * 1048576 ) {
				self::add( 'settings', 'warning', 'The error log is huge (' . self::bytes( $size ) . ')', 'Something is writing lots of errors - every error written costs time - and the file is using disk space.', 'Read the last lines to see which plugin is causing the errors (the file paths say), fix that, then delete ' . str_replace( ABSPATH, '', $log ) . '.', 40 );
			}
		}
		if ( ! defined( 'WP_POST_REVISIONS' ) || true === WP_POST_REVISIONS || -1 === WP_POST_REVISIONS ) {
			self::add( 'settings', 'info', 'Unlimited post revisions', 'WordPress keeps every saved version of every post forever, which slowly bloats the database.', 'Add define( \'WP_POST_REVISIONS\', 10 ); to wp-config.php.' );
		}
		if ( function_exists( 'disk_free_space' ) ) {
			$free = @disk_free_space( ABSPATH ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
			if ( false !== $free && $free < 1073741824 ) {
				self::add( 'server', 'warning', 'Low disk space (' . self::bytes( $free ) . ' free)', 'When the disk fills up, caching, uploads, updates and the database stop working.', 'Delete old backups and logs, or ask your host for more space.' );
			}
		}
	}
}
