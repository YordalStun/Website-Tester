<?php
/**
 * Turns the measurements into a report: findings, a plain-English diagnosis, the admin page HTML and the
 * "Export for AI" file.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_Report {

	const SEVERITY_ORDER = array( 'critical' => 0, 'warning' => 1, 'info' => 2, 'good' => 3 );
	const SEVERITY_LABEL = array( 'critical' => 'Problem', 'warning' => 'Needs attention', 'info' => 'Note', 'good' => 'Good' );
	const CATEGORIES     = array(
		'page'     => array( 'Page speed (built from scratch)', 3 ),
		'plugins'  => array( 'Plugins & theme', 3 ),
		'external' => array( 'Calls to other websites', 2 ),
		'server'   => array( 'Server & PHP', 2 ),
		'database' => array( 'Database', 2 ),
		'caching'  => array( 'Caching', 2 ),
		'cron'     => array( 'Scheduled tasks (WP-Cron)', 1 ),
		'settings' => array( 'WordPress settings', 1 ),
	);

	public static function finding( $category, $severity, $title, $detail = '', $fix = '', $impact = 0, $items = array() ) {
		return array(
			'category' => $category,
			'severity' => $severity,
			'title'    => $title,
			'detail'   => $detail,
			'fix'      => $fix,
			'impact'   => (int) $impact,
			'items'    => array_values( (array) $items ),
		);
	}

	private static function median( $values ) {
		$values = array_values( array_filter( $values, 'is_numeric' ) );
		if ( ! $values ) {
			return 0;
		}
		sort( $values );
		$n = count( $values );
		return $n % 2 ? $values[ (int) ( $n / 2 ) ] : ( $values[ $n / 2 - 1 ] + $values[ $n / 2 ] ) / 2;
	}

	private static function ms( $ms ) {
		return $ms >= 1000 ? number_format_i18n( $ms / 1000, 1 ) . ' s' : number_format_i18n( $ms ) . ' ms';
	}

	private static function host( $url ) {
		$host = wp_parse_url( $url, PHP_URL_HOST );
		return $host ? $host : $url;
	}

	public static function clean_url( $url ) {
		return remove_query_arg( array( JJSI_Profiler::PARAM_TOKEN, JJSI_Profiler::PARAM_RUN, 'jjsi_nocache' ), $url );
	}

	// ----------------------------------------------------------------------------------------------------
	// Names for owners
	// ----------------------------------------------------------------------------------------------------

	private static $labels = null;

	public static function owner_label( $owner ) {
		if ( null === self::$labels ) {
			self::$labels = array();
			if ( ! function_exists( 'get_plugins' ) ) {
				require_once ABSPATH . 'wp-admin/includes/plugin.php';
			}
			foreach ( get_plugins() as $file => $info ) {
				$folder = false === strpos( $file, '/' ) ? $file : dirname( $file );
				self::$labels[ 'plugin:' . $folder ] = $info['Name'];
			}
			foreach ( wp_get_themes() as $slug => $theme ) {
				self::$labels[ 'theme:' . $slug ] = $theme->get( 'Name' ) . ' (theme)';
			}
		}
		if ( isset( self::$labels[ $owner ] ) ) {
			return self::$labels[ $owner ];
		}
		$fixed = array( 'core' => 'WordPress core', 'other' => 'Other code', 'unknown' => 'Unknown' );
		if ( isset( $fixed[ $owner ] ) ) {
			return $fixed[ $owner ];
		}
		$parts = explode( ':', $owner, 2 );
		if ( 2 === count( $parts ) ) {
			$kinds = array( 'mu' => 'Must-use plugin: ', 'dropin' => 'Drop-in: ', 'plugin' => 'Plugin: ', 'theme' => 'Theme: ' );
			return ( isset( $kinds[ $parts[0] ] ) ? $kinds[ $parts[0] ] : '' ) . $parts[1];
		}
		return $owner;
	}

	private static function folder_of( $owner ) {
		return 0 === strpos( $owner, 'plugin:' ) ? substr( $owner, 7 ) : '';
	}

	// ----------------------------------------------------------------------------------------------------
	// Build
	// ----------------------------------------------------------------------------------------------------

	/**
	 * @param array $runs   url => list of profiler results.
	 * @param array $visits url => list of normal (cacheable) visit timings.
	 * @param array $checks JJSI_Checks::run() output.
	 * @param array $notes  Things that went wrong while testing.
	 */
	public static function build( $runs, $visits, $checks, $notes = array(), $baseline = array() ) {
		$findings = isset( $checks['findings'] ) ? $checks['findings'] : array();
		$pages    = array();
		$cron_spawns  = array(); // WordPress calling its own wp-cron.php during a page load
		$rewrite_runs = 0;
		$run_count    = 0;
		$home_host    = self::host( home_url() );
		$is_cron_call = function ( $call ) use ( $home_host ) {
			return false !== strpos( $call['url'], 'wp-cron.php' ) && self::host( $call['url'] ) === $home_host;
		};
		$external_ms = function ( $run ) use ( $is_cron_call ) {
			$ms = 0;
			foreach ( (array) $run['http'] as $call ) {
				$ms += $is_cron_call( $call ) ? 0 : $call['ms'];
			}
			return $ms;
		};
		$owners   = array(); // owner => list of per-page medians
		$q_owner  = array();
		$all_queries = array();
		$http     = array();
		$errors   = array();
		$early    = true;

		foreach ( $runs as $url => $list ) {
			$list = array_values( array_filter( (array) $list ) );
			if ( ! $list ) {
				continue;
			}
			$per_owner = array();
			$per_owner_q = array();
			foreach ( $list as $run ) {
				$run_count++;
				$rewrite_runs += empty( $run['rewrite_rebuilt'] ) ? 0 : 1;
				$early = $early && ! empty( $run['early'] );
				foreach ( (array) $run['owners'] as $owner => $info ) {
					$per_owner[ $owner ][] = $info['load_ms'] + $info['hooks_ms'];
				}
				$counted = array();
				foreach ( (array) $run['queries'] as $q ) {
					$counted[ $q['owner'] ][0] = ( isset( $counted[ $q['owner'] ][0] ) ? $counted[ $q['owner'] ][0] : 0 ) + 1;
					$counted[ $q['owner'] ][1] = ( isset( $counted[ $q['owner'] ][1] ) ? $counted[ $q['owner'] ][1] : 0 ) + $q['ms'];
					$norm = preg_replace( array( "/'(?:[^'\\\\]|\\\\.)*'/", '/\b\d+\b/' ), array( "'?'", '?' ), $q['sql'] );
					if ( ! isset( $all_queries[ $norm ] ) ) {
						$all_queries[ $norm ] = array( 'sql' => $q['sql'], 'owner' => $q['owner'], 'caller' => $q['caller'], 'max_ms' => 0, 'count' => 0, 'pages' => array() );
					}
					$all_queries[ $norm ]['max_ms'] = max( $all_queries[ $norm ]['max_ms'], $q['ms'] );
					$all_queries[ $norm ]['count']++;
					$all_queries[ $norm ]['pages'][ $url ] = true;
				}
				foreach ( $counted as $owner => $c ) {
					$per_owner_q[ $owner ][] = $c;
				}
				foreach ( (array) $run['http'] as $call ) {
					if ( $is_cron_call( $call ) ) {
						$cron_spawns[] = array( 'ms' => $call['ms'], 'page' => self::clean_url( $url ), 'total_ms' => $run['total_ms'] );
						continue;
					}
					$key = $call['owner'] . ' ' . $call['method'] . ' ' . preg_replace( '/\?.*$/', '', $call['url'] );
					if ( ! isset( $http[ $key ] ) ) {
						$http[ $key ] = array( 'url' => $call['url'], 'method' => $call['method'], 'owner' => $call['owner'], 'times' => array(), 'status' => $call['status'], 'pages' => array() );
					}
					$http[ $key ]['times'][] = $call['ms'];
					$http[ $key ]['pages'][ $url ] = true;
				}
				foreach ( (array) $run['errors'] as $error ) {
					$key = $error['file'];
					if ( ! isset( $errors[ $key ] ) ) {
						$errors[ $key ] = $error;
					}
				}
			}
			foreach ( $per_owner as $owner => $values ) {
				$owners[ $owner ][ $url ] = self::median( array_pad( $values, count( $list ), 0 ) );
			}
			foreach ( $per_owner_q as $owner => $values ) {
				$q_owner[ $owner ][ $url ] = array( self::median( wp_list_pluck( $values, 0 ) ), self::median( wp_list_pluck( $values, 1 ) ) );
			}
			$visit_times = isset( $visits[ $url ] ) ? wp_list_pluck( $visits[ $url ], 'ms' ) : array();
			$pages[ $url ] = array(
				'url'         => self::clean_url( $url ),
				'runs'        => count( $list ),
				'total_ms'    => self::median( wp_list_pluck( $list, 'total_ms' ) ),
				'max_ms'      => max( wp_list_pluck( $list, 'total_ms' ) ),
				'startup_ms'  => self::median( wp_list_pluck( $list, 'startup_ms' ) ),
				'template_ms' => self::median( wp_list_pluck( $list, 'template_ms' ) ),
				'queries'     => self::median( wp_list_pluck( $list, 'query_count' ) ),
				'query_ms'    => self::median( array_map( function ( $r ) { return array_sum( wp_list_pluck( (array) $r['queries'], 'ms' ) ); }, $list ) ),
				'http_ms'     => self::median( array_map( $external_ms, $list ) ),
				'memory_mb'   => max( wp_list_pluck( $list, 'memory_mb' ) ),
				'status'      => $list[0]['status'],
				'cached_ms'   => $visit_times ? self::median( $visit_times ) : null,
				'cache_header' => isset( $visits[ $url ][0]['cache'] ) ? $visits[ $url ][0]['cache'] : '',
				'template_owner' => $list[0]['template_owner'],
			);
		}
		$page_count = max( 1, count( $pages ) );

		// Average per page across the pages tested
		$owner_rows = array();
		foreach ( $owners as $owner => $by_page ) {
			$ms = array_sum( $by_page ) / $page_count;
			$q  = isset( $q_owner[ $owner ] ) ? $q_owner[ $owner ] : array();
			$owner_rows[ $owner ] = array(
				'owner'    => $owner,
				'label'    => self::owner_label( $owner ),
				'ms'       => round( $ms, 1 ),
				'queries'  => $q ? round( array_sum( wp_list_pluck( $q, 0 ) ) / $page_count ) : 0,
				'query_ms' => $q ? round( array_sum( wp_list_pluck( $q, 1 ) ) / $page_count, 1 ) : 0,
				'http'     => array(),
				'errors'   => 0,
				'hooks'    => array(),
				'load_ms'  => 0,
			);
		}
		// Busiest hooks and load time from the slowest run of each owner (for detail text)
		foreach ( $runs as $list ) {
			foreach ( (array) $list as $run ) {
				foreach ( (array) $run['owners'] as $owner => $info ) {
					if ( isset( $owner_rows[ $owner ] ) ) {
						foreach ( (array) $info['top_hooks'] as $hook => $ms ) {
							$owner_rows[ $owner ]['hooks'][ $hook ] = max( isset( $owner_rows[ $owner ]['hooks'][ $hook ] ) ? $owner_rows[ $owner ]['hooks'][ $hook ] : 0, $ms );
						}
						$owner_rows[ $owner ]['load_ms'] = max( $owner_rows[ $owner ]['load_ms'], $info['load_ms'] );
					}
				}
			}
		}
		foreach ( $http as $call ) {
			if ( isset( $owner_rows[ $call['owner'] ] ) ) {
				$owner_rows[ $call['owner'] ]['http'][] = self::host( $call['url'] );
			}
		}
		foreach ( $errors as $error ) {
			if ( isset( $owner_rows[ $error['owner'] ] ) ) {
				$owner_rows[ $error['owner'] ]['errors']++;
			}
		}
		uasort( $owner_rows, function ( $a, $b ) { return $b['ms'] <=> $a['ms']; } );

		$avg = function ( $key ) use ( $pages, $page_count ) {
			return array_sum( wp_list_pluck( $pages, $key ) ) / $page_count;
		};
		$total    = $avg( 'total_ms' );
		$startup  = $avg( 'startup_ms' );
		$template = $avg( 'template_ms' );
		$cached   = array_filter( wp_list_pluck( $pages, 'cached_ms' ), 'is_numeric' );
		$cached   = $cached ? array_sum( $cached ) / count( $cached ) : null;
		// Normal visits are made from the server to itself, so they include the time it takes the server to reach
		// its own website. Take that off (measured with a tiny static file) so slow self-connections aren't
		// mistaken for slow pages.
		$base_times = array();
		foreach ( (array) $baseline as $b ) {
			if ( empty( $b['error'] ) && isset( $b['ms'] ) ) {
				$base_times[] = $b['ms'];
			}
		}
		$base = array(
			'median' => $base_times ? round( self::median( $base_times ) ) : null,
			'max'    => $base_times ? round( max( $base_times ) ) : null,
			'errors' => count( (array) $baseline ) - count( $base_times ),
		);
		$cached_raw = $cached;
		if ( null !== $cached && null !== $base['median'] ) {
			$cached = max( 0, $cached - $base['median'] );
		}

		// Where the time goes on an average page
		$plugin_ms = 0;
		$theme_ms  = $template;
		foreach ( $owner_rows as $row ) {
			if ( 0 === strpos( $row['owner'], 'theme:' ) ) {
				$theme_ms += $row['ms'];
			} elseif ( 'core' !== $row['owner'] ) {
				$plugin_ms += $row['ms'];
			}
		}
		$core_ms   = max( 0, $total - $startup - $plugin_ms - $theme_ms );
		$breakdown = array(
			array( 'label' => 'PHP & WordPress starting up', 'ms' => round( $startup ), 'key' => 'startup' ),
			array( 'label' => 'Plugins', 'ms' => round( $plugin_ms ), 'key' => 'plugins' ),
			array( 'label' => 'Theme & page template', 'ms' => round( $theme_ms ), 'key' => 'theme' ),
			array( 'label' => 'WordPress core', 'ms' => round( $core_ms ), 'key' => 'core' ),
		);

		$cdata = isset( $checks['data'] ) ? $checks['data'] : array();
		$cron = array(
			'count'  => count( $cron_spawns ),
			'max_ms' => $cron_spawns ? round( max( wp_list_pluck( $cron_spawns, 'ms' ) ) ) : 0,
			'visits' => $cron_spawns,
		);
		$ctx = array( 'baseline' => $base, 'cron' => $cron, 'rewrite_runs' => $rewrite_runs, 'run_count' => $run_count, 'cached_raw' => $cached_raw );
		$findings = array_merge( $findings, self::page_findings( $pages, $owner_rows, round( $total ), $cached, $cdata, $early, $all_queries, $http, $errors, $page_count, $ctx ) );
		if ( $notes ) {
			$findings[] = self::finding( 'page', 'info', 'Some test visits didn\'t work', 'Results are based on the visits that did work.', '', 0, $notes );
		}
		usort(
			$findings,
			function ( $a, $b ) {
				$s = self::SEVERITY_ORDER[ $a['severity'] ] <=> self::SEVERITY_ORDER[ $b['severity'] ];
				return $s ? $s : $b['impact'] <=> $a['impact'];
			}
		);

		uasort( $all_queries, function ( $a, $b ) { return $b['max_ms'] <=> $a['max_ms']; } );
		$http_rows = array();
		foreach ( $http as $call ) {
			$call['ms']    = round( self::median( $call['times'] ), 1 );
			$call['label'] = self::owner_label( $call['owner'] );
			$call['pages'] = count( $call['pages'] );
			unset( $call['times'] );
			$http_rows[] = $call;
		}
		usort( $http_rows, function ( $a, $b ) { return $b['ms'] <=> $a['ms']; } );

		$report = array(
			'version'    => JJSI_VERSION,
			'created'    => time(),
			'site'       => home_url( '/' ),
			'wp_version' => get_bloginfo( 'version' ),
			'php'        => PHP_VERSION,
			'early'      => $early,
			'pages'      => array_values( $pages ),
			'total_ms'   => round( $total ),
			'cached_ms'  => null === $cached ? null : round( $cached ),
			'cached_raw_ms' => null === $cached_raw ? null : round( $cached_raw ),
			'baseline'   => $base,
			'cron_spawns' => $cron,
			'rewrite_runs' => $rewrite_runs,
			'run_count'  => $run_count,
			'breakdown'  => $breakdown,
			'owners'     => array_values( $owner_rows ),
			'queries'    => array_slice( array_values( array_map( function ( $q ) { $q['pages'] = count( $q['pages'] ); return $q; }, $all_queries ) ), 0, 40 ),
			'http'       => $http_rows,
			'errors'     => array_values( $errors ),
			'findings'   => $findings,
			'checks'     => $cdata,
		);
		$report['diagnosis'] = self::diagnose( $report );
		$report['score']     = self::score( $findings );
		$report['grade']     = self::grade( $report['score'] );
		return $report;
	}

	private static function page_findings( $pages, $owner_rows, $total, $cached, $cdata, $early, $all_queries, $http, $errors, $page_count, $ctx ) {
		$out = array();
		if ( ! $pages ) {
			$out[] = self::finding( 'page', 'critical', 'No pages could be tested', 'Speed Inspector couldn\'t load any of the site\'s pages, so only the server and database checks below were run.', 'Check the site loads for logged-out visitors. If a security plugin or firewall blocks requests from the server to itself, allow them (WordPress\'s own Site Health "loopback" test needs this too).', 1000 );
			return $out;
		}
		$slowest = max( wp_list_pluck( $pages, 'total_ms' ) );
		$detail  = sprintf( 'With no page cache, WordPress takes about %s to build an average page (slowest page tested: %s). This is what first-time visitors after a cache clear, logged-in users, shoppers with a cart, and search pages get.', self::ms( $total ), self::ms( $slowest ) );
		if ( $total > 1500 ) {
			$out[] = self::finding( 'page', 'critical', 'Pages are very slow to build (' . self::ms( $total ) . ')', $detail, 'See the main problem at the top and the plugin timings below.', (int) $total );
		} elseif ( $total > 600 ) {
			$out[] = self::finding( 'page', 'warning', 'Pages are slow to build (' . self::ms( $total ) . ')', $detail . ' A lean WordPress site does this in under 300 ms.', 'See the main problem at the top and the plugin timings below.', (int) ( $total / 2 ) );
		} else {
			$out[] = self::finding( 'page', 'good', 'Pages are built quickly (' . self::ms( $total ) . ')', $detail );
		}

		// Page cache: is one switched on, and do normal visits actually come back faster?
		$pc         = isset( $cdata['page_cache'] ) ? $cdata['page_cache'] : array();
		$page_cache = isset( $pc['active'] ) ? $pc['active'] : ( ! empty( $pc['plugins'] ) || ! empty( $pc['advanced_cache'] ) );
		$base       = $ctx['baseline'];
		$adjusted   = null !== $base['median'] && $base['median'] > 50
			? sprintf( ' (%s measured, minus %s the server takes just to reach its own website)', self::ms( $ctx['cached_raw'] ), self::ms( $base['median'] ) )
			: '';
		$fast = null !== $cached && $cached < 0.5 * $total && $cached < 400;
		if ( $fast ) {
			$out[] = self::finding( 'caching', 'good', 'Normal visits come back fast (' . self::ms( $cached ) . ')' . ( $page_cache ? ' - page caching is working' : '' ), sprintf( 'Normal visits are served in about %s%s instead of the %s it takes to build a page.', self::ms( $cached ), $adjusted, self::ms( $total ) ) );
		} elseif ( ! $page_cache ) {
			$out[] = self::finding(
				'caching', $total > 800 ? 'critical' : 'warning', 'No page caching - every visit builds the page from scratch',
				sprintf( 'No page cache is switched on, so every visitor waits while WordPress builds the page (about %s here, more when the server is busy). A page cache saves the finished page and serves it in a few milliseconds.', self::ms( $total ) ),
				'Install ONE caching plugin. On LiteSpeed servers: LiteSpeed Cache. SiteGround: Speed Optimizer. Otherwise: WP Super Cache (free), WP Fastest Cache (free) or WP Rocket (paid). Ask your host first - some include server caching.',
				(int) $total
			);
		} elseif ( null !== $cached && $cached > 300 ) {
			$out[] = self::finding(
				'caching', $cached > 800 ? 'critical' : 'warning', 'The page cache isn\'t helping (normal visits take ' . self::ms( $cached ) . ')',
				'Page caching is switched on, but normal visits are still slow' . $adjusted . '. The cache may be bypassed (by a cookie or a plugin), exclude these pages, or be cleared too often.',
				'Open your caching plugin\'s settings, check page caching is enabled and these pages aren\'t excluded. Clear the cache and run this test again.',
				(int) $cached
			);
		}

		// The server reaching its own website (WP-Cron, Site Health and some plugins depend on this)
		if ( null !== $base['median'] && ( $base['median'] > 300 || $base['max'] > 2000 ) ) {
			$five = $base['max'] >= 4800 && $base['max'] <= 5600;
			$out[] = self::finding(
				'server', $base['max'] >= 2000 || $base['median'] > 1000 ? 'critical' : 'warning',
				sprintf( 'The server is slow to reach its own website (%s on average, up to %s)', self::ms( $base['median'] ), self::ms( $base['max'] ) ),
				'Fetching a tiny image from this site, from the server itself, should take a few milliseconds. WordPress does this kind of request during page loads to start its scheduled tasks (WP-Cron), and Site Health and some plugins do it too, so visitors end up waiting for it.'
				. ( $five ? ' Delays of almost exactly 5 seconds are the classic sign of a slow or broken DNS server in the server\'s settings.' : '' ),
				'Ask your host to check that the server can reach its own domain quickly: the server\'s DNS resolver settings (/etc/resolv.conf), IPv6, and the firewall. A common fix is adding the domain to the server\'s /etc/hosts. If the site is behind Cloudflare or another proxy, ask them to route requests to itself locally.',
				(int) $base['max']
			);
		}

		// WP-Cron started during a visit, making that visitor wait
		$cron          = $ctx['cron'];
		$cron_disabled = ! empty( $cdata['cron']['disabled'] );
		if ( ! $cron['count'] && ! $cron_disabled && null !== $base['max'] && $base['max'] >= 500 ) {
			// Not caught in the act during this test, but WP-Cron starts by calling the site itself, so whenever
			// tasks are due the visitor waits about as long as that takes.
			$out[] = self::finding(
				'cron', $base['max'] >= 1000 ? 'critical' : 'warning',
				sprintf( 'Scheduled tasks (WP-Cron) will make visitors wait up to about %s', self::ms( $base['max'] ) ),
				'WordPress starts its scheduled tasks during a visitor\'s page load by calling wp-cron.php on this same site. This server takes up to ' . self::ms( $base['max'] ) . ' to reach its own website, so whenever tasks are due (at most once a minute) the visitor who happens to load a page waits about that long.'
				. ( ! empty( $cdata['cron']['frequent'] ) ? ' Some tasks run every minute (' . implode( ', ', array_slice( array_keys( $cdata['cron']['frequent'] ), 0, 3 ) ) . '), so this happens to a visitor roughly every minute.' : '' ),
				'Stop page visits from starting scheduled tasks: add define( \'DISABLE_WP_CRON\', true ); to wp-config.php, and create a real cron job in your hosting control panel that runs every 5 minutes: wget -q -O - ' . site_url( 'wp-cron.php?doing_wp_cron' ) . ' >/dev/null 2>&1. Also ask your host why the server is slow to reach its own website.',
				(int) $base['max']
			);
		}
		if ( $cron['count'] && $cron['max_ms'] >= 200 ) {
			$frequent = ! empty( $cdata['cron']['frequent'] ) ? array_keys( $cdata['cron']['frequent'] ) : array();
			$items    = array();
			foreach ( $cron['visits'] as $v ) {
				$items[] = sprintf( '%s - waited %s (whole page: %s)', $v['page'], self::ms( $v['ms'] ), self::ms( $v['total_ms'] ) );
			}
			$out[] = self::finding(
				'cron', $cron['max_ms'] >= 1000 ? 'critical' : 'warning',
				sprintf( 'Scheduled tasks (WP-Cron) make visitors wait up to %s', self::ms( $cron['max_ms'] ) ),
				sprintf( 'In %d of %d test visits, WordPress started its scheduled tasks by calling wp-cron.php on this same site, and the visitor\'s page had to wait for that. It\'s meant to take a fraction of a second.', $cron['count'], $ctx['run_count'] )
				. ( $frequent ? ' Some tasks run every minute (' . implode( ', ', array_slice( $frequent, 0, 3 ) ) . '), so this happens to a visitor roughly every minute.' : '' ),
				'Stop page visits from starting scheduled tasks: add define( \'DISABLE_WP_CRON\', true ); to wp-config.php, and create a real cron job in your hosting control panel that runs every 5 minutes: wget -q -O - ' . site_url( 'wp-cron.php?doing_wp_cron' ) . ' >/dev/null 2>&1. Also see "slow to reach its own website" if that is reported.',
				(int) $cron['max_ms'], $items
			);
		}

		// Rewrite rules (permalinks) rebuilt on every page load - normally only when settings change
		if ( $ctx['rewrite_runs'] >= 2 && $ctx['rewrite_runs'] * 2 >= $ctx['run_count'] ) {
			$found = JJSI_Checks::find_code( 'flush_rewrite_rules' );
			$out[] = self::finding(
				'plugins', 'warning', 'Permalinks are rebuilt on every page load',
				sprintf( 'In %d of %d test visits, WordPress rebuilt all its permalink rules from scratch (and older WordPress versions also save them to the database every time). That should only happen when permalink settings change. Something is calling flush_rewrite_rules() on every page load, which wastes time on every visit.', $ctx['rewrite_runs'], $ctx['run_count'] ),
				'Look at the plugins/theme listed below: one of them calls flush_rewrite_rules() on every page load instead of only when it\'s activated. Update it, or ask its author to fix it. (Calls inside activation code are normal.)',
				60, $found
			);
		}

		// Plugins
		foreach ( $owner_rows as $row ) {
			if ( 'core' === $row['owner'] || $row['ms'] < 100 ) {
				continue;
			}
			$share = $total > 0 ? round( $row['ms'] * 100 / $total ) : 0;
			$bits  = array();
			if ( $row['load_ms'] > 20 ) {
				$bits[] = 'loading its files: ' . self::ms( $row['load_ms'] );
			}
			arsort( $row['hooks'] );
			foreach ( array_slice( $row['hooks'], 0, 4, true ) as $hook => $ms ) {
				if ( $ms >= 5 ) {
					$bits[] = 'during "' . $hook . '": ' . self::ms( $ms );
				}
			}
			if ( $row['queries'] ) {
				$bits[] = sprintf( '%d database queries (%s)', $row['queries'], self::ms( $row['query_ms'] ) );
			}
			if ( $row['http'] ) {
				$bits[] = 'calls to ' . implode( ', ', array_unique( $row['http'] ) );
			}
			if ( $row['errors'] ) {
				$bits[] = $row['errors'] . ' PHP warnings/errors';
			}
			$folder = self::folder_of( $row['owner'] );
			$advice = isset( JJSI_Known::$advice[ $folder ] ) ? JJSI_Known::$advice[ $folder ][1] . ' ' : '';
			$out[]  = self::finding(
				0 === strpos( $row['owner'], 'theme:' ) ? 'plugins' : 'plugins',
				$row['ms'] >= 300 ? 'critical' : 'warning',
				sprintf( '%s adds %s to every page (%d%% of the time)', $row['label'], self::ms( $row['ms'] ), $share ),
				'Where its time goes: ' . ( $bits ? implode( '; ', $bits ) : 'spread across many small tasks' ) . '.',
				$advice . 'Check its settings for features you don\'t use. To confirm, deactivate it and run this test again - the page time should drop by about this much. If you need it, look for a lighter alternative.',
				(int) $row['ms']
			);
		}

		// Calls to other websites
		if ( $http ) {
			$items     = array();
			$per_page  = 0;
			foreach ( $http as $call ) {
				$ms        = self::median( $call['times'] );
				$per_page += $ms * count( $call['pages'] ) / $page_count;
				$items[]   = sprintf( '%s %s - %s, from %s (status: %s)', $call['method'], substr( $call['url'], 0, 120 ), self::ms( $ms ), self::owner_label( $call['owner'] ), $call['status'] );
			}
			$sev   = $per_page > 500 ? 'critical' : ( $per_page > 100 ? 'warning' : 'info' );
			$out[] = self::finding(
				'external', $sev, sprintf( 'Pages wait for other websites (%s per page)', self::ms( $per_page ) ),
				'While building the page, the server contacts other websites and waits for their answers. If that service is slow or down, your site is slow or down too.',
				'Plugins should cache these answers instead of asking every time. Check the plugin\'s settings for a cache option, update it, or replace it. Licence checks and feeds are the usual culprits.',
				(int) $per_page, $items
			);
		} else {
			$out[] = self::finding( 'external', 'good', 'Pages don\'t wait for other websites' );
		}

		// Database work during page loads
		$query_ms = array_sum( wp_list_pluck( $pages, 'query_ms' ) ) / $page_count;
		$queries  = array_sum( wp_list_pluck( $pages, 'queries' ) ) / $page_count;
		$slow     = array();
		$dupes    = array();
		foreach ( $all_queries as $q ) {
			if ( $q['max_ms'] >= 50 && count( $slow ) < 12 ) {
				$slow[] = sprintf( '%s - %s (%s): %s', self::ms( $q['max_ms'] ), self::owner_label( $q['owner'] ), $q['caller'], substr( $q['sql'], 0, 160 ) );
			}
		}
		if ( $slow ) {
			$out[] = self::finding( 'database', count( $slow ) > 3 ? 'critical' : 'warning', count( $slow ) . ' slow database queries', 'These single queries took 50 ms or more. The plugin/theme that ran each one is shown.', 'Update the plugin responsible. Huge tables (see below) and missing indexes are common causes - "Index WP MySQL For Speed" adds better indexes to WordPress\'s tables.', 150, $slow );
		}
		if ( $queries > 200 || $query_ms > 300 ) {
			$out[] = self::finding( 'database', $query_ms > 500 ? 'critical' : 'warning', sprintf( 'Lots of database work per page (%d queries, %s)', $queries, self::ms( $query_ms ) ), 'The plugin table shows which plugins run the most queries.', 'An object cache (Redis) avoids repeating most of these. Then deal with the plugins running the most queries.', (int) $query_ms );
		}
		if ( $errors ) {
			$items = array();
			foreach ( array_slice( $errors, 0, 15 ) as $e ) {
				$items[] = sprintf( '%s: %s - %s (%s)', $e['type'], $e['message'], $e['file'], self::owner_label( $e['owner'] ) );
			}
			$out[] = self::finding( 'plugins', 'warning', count( $errors ) . ' PHP warnings/errors while building pages', 'Each warning costs time (especially when logging is on) and usually means a plugin is outdated or not compatible with your PHP version.', 'Update the plugins named below. If they\'re up to date, report it to the plugin\'s author.', 60, $items );
		}
		$memory = max( wp_list_pluck( $pages, 'memory_mb' ) );
		if ( $memory > 256 ) {
			$out[] = self::finding( 'server', 'warning', sprintf( 'Pages use a lot of memory (%s MB)', $memory ), 'Heavy memory use means a lot of code is running and can cause "allowed memory size exhausted" errors.', 'The plugin timings show the heaviest plugins.' );
		}
		foreach ( $pages as $p ) {
			if ( $p['status'] >= 400 ) {
				$out[] = self::finding( 'page', 'warning', sprintf( 'A test page returned an error (%d)', $p['status'] ), $p['url'] );
			}
		}
		if ( ! $early ) {
			$out[] = self::finding( 'settings', 'info', 'Plugin loading times couldn\'t be measured', 'Speed Inspector couldn\'t install its small early loader in wp-content/mu-plugins (the folder isn\'t writable), so the time each plugin takes to load its files isn\'t included. Everything else is.', 'Make wp-content/mu-plugins writable, then deactivate and reactivate Speed Inspector.' );
		}
		return $out;
	}

	private static function diagnose( $r ) {
		$causes = array();
		$total  = $r['total_ms'];
		$checks = $r['checks'];
		foreach ( array_slice( $r['owners'], 0, 3 ) as $row ) {
			if ( 'core' !== $row['owner'] && $row['ms'] >= 100 ) {
				$extra = $row['http'] ? ' Much of it is waiting for ' . implode( ', ', array_unique( $row['http'] ) ) . '.' : '';
				$causes[] = array( $row['label'], $row['ms'], sprintf( '%s adds about %s to every page that isn\'t cached (%d%% of the time WordPress spends building the page).%s Deactivate it and test again to confirm, then check its settings or replace it.', $row['label'], self::ms( $row['ms'] ), $total ? round( $row['ms'] * 100 / $total ) : 0, $extra ) );
			}
		}
		$http_ms = 0;
		foreach ( $r['http'] as $call ) {
			$http_ms += $call['ms'] * $call['pages'] / max( 1, count( $r['pages'] ) );
		}
		if ( $http_ms > 100 ) {
			$hosts = array_unique( array_map( function ( $c ) { return wp_parse_url( $c['url'], PHP_URL_HOST ); }, $r['http'] ) );
			$causes[] = array( 'Waiting for other websites', $http_ms * 0.9, sprintf( 'Each page waits about %s for other websites (%s) before it can be sent. A plugin should cache those answers instead of asking on every page.', self::ms( $http_ms ), implode( ', ', array_slice( $hosts, 0, 4 ) ) ) );
		}
		if ( ! empty( $checks['cpu_ratio'] ) && $checks['cpu_ratio'] > 2 && $total > 0 ) {
			$causes[] = array( 'Slow server', $total * ( 1 - 1 / $checks['cpu_ratio'] ), sprintf( 'The server\'s processor is %.1f times slower than a typical good WordPress host, so everything WordPress does takes longer. This is the hosting plan, not a plugin.', $checks['cpu_ratio'] ) );
		}
		if ( isset( $checks['opcache'] ) && false === $checks['opcache'] && 'cli' !== PHP_SAPI ) {
			$causes[] = array( 'PHP OPcache is off', $total * 0.5, 'PHP OPcache is switched off, so PHP re-compiles all of WordPress and every plugin for every page. Turning it on in the hosting control panel typically halves the time.' );
		}
		if ( ! empty( $checks['db_query_ms'] ) && $checks['db_query_ms'] > 0.6 ) {
			$queries = $r['pages'] ? array_sum( wp_list_pluck( $r['pages'], 'queries' ) ) / count( $r['pages'] ) : 0;
			$causes[] = array( 'Slow database server', $checks['db_query_ms'] * $queries, sprintf( 'Every database query takes about %.1f ms even when it\'s trivial, and pages run about %d of them.', $checks['db_query_ms'], $queries ) );
		}
		$page_cache = ! empty( $checks['page_cache']['active'] );
		$cached     = $r['cached_ms'];
		if ( ! $page_cache && ( null === $cached || $cached > 0.5 * $total ) && $total > 150 ) {
			$causes[] = array( 'No page caching', $total * 0.6, sprintf( 'No page cache is switched on, so every visitor waits about %s while WordPress builds the page from scratch. A page cache would serve most visits in a few milliseconds.', self::ms( $total ) ) );
		} elseif ( $page_cache && null !== $cached && $cached > 300 && $cached > 0.5 * $total ) {
			$causes[] = array( 'The page cache isn\'t working', $cached * 0.8, sprintf( 'Page caching is switched on, but normal visits still take about %s - pages aren\'t being served from the cache.', self::ms( $cached ) ) );
		}
		$cron = isset( $r['cron_spawns'] ) ? $r['cron_spawns'] : array( 'count' => 0, 'max_ms' => 0 );
		$base = isset( $r['baseline'] ) ? $r['baseline'] : array( 'median' => null, 'max' => null );
		$slow_self = null !== $base['max'] && ( $base['max'] > 2000 || $base['median'] > 300 );
		$cron_disabled = ! empty( $checks['cron']['disabled'] );
		$cron_wait     = $cron['max_ms'] >= 500 ? $cron['max_ms'] : ( ! $cron_disabled && $slow_self ? $base['max'] : 0 );
		if ( $cron_wait >= 500 ) {
			$causes[] = array( 'Scheduled tasks (WP-Cron) making visitors wait', $cron_wait * 0.8, sprintf( 'Whenever scheduled tasks are due, the visitor who happens to load a page waits while WordPress starts them - up to %s %s, when it should take a fraction of a second.%s Fix: switch WP-Cron off for page visits and run it from a real cron job instead (see Scheduled tasks below).', self::ms( $cron_wait ), $cron['max_ms'] >= 500 ? 'in this test' : 'on this server', $slow_self ? ' The underlying problem is that the server is slow to reach its own website (see Server).' : '' ) );
		} elseif ( $slow_self ) {
			$causes[] = array( 'The server is slow to reach its own website', $base['max'] * 0.3, sprintf( 'Requests from the server to its own website take up to %s. WordPress makes these to start scheduled tasks, so some visitors end up waiting for them. Ask your host to check the server\'s DNS settings.', self::ms( $base['max'] ) ) );
		}
		if ( ! empty( $checks['autoload']['bytes'] ) && $checks['autoload']['bytes'] > 800 * 1024 ) {
			$causes[] = array( 'Too much autoloaded data', $checks['autoload']['bytes'] / 1048576 * 60, sprintf( '%s of settings are loaded from the database on every page. The biggest are listed in the Database section.', JJSI_Checks::bytes( $checks['autoload']['bytes'] ) ) );
		}
		foreach ( $r['breakdown'] as $row ) {
			if ( 'theme' === $row['key'] && $row['ms'] > 250 ) {
				$causes[] = array( 'Theme', $row['ms'], sprintf( 'The theme and its page templates take about %s per page.', self::ms( $row['ms'] ) ) );
			}
		}
		usort( $causes, function ( $a, $b ) { return $b[1] <=> $a[1]; } );
		$paragraphs = array();
		if ( $r['pages'] ) {
			$slowest = max( wp_list_pluck( $r['pages'], 'max_ms' ) );
			$paragraphs[] = sprintf(
				'Building a page from scratch takes about %s%s%s.',
				self::ms( $total ),
				null !== $cached ? sprintf( '; a normal visit took %s', self::ms( $cached ) ) : '',
				$slowest > 2 * $total && $slowest > 1500 ? sprintf( ' - but the slowest test visit took %s', self::ms( $slowest ) ) : ''
			);
		}
		if ( ! $causes ) {
			return array( 'headline' => $r['pages'] ? 'No major speed problems found inside WordPress' : 'Pages couldn\'t be tested', 'paragraphs' => $paragraphs, 'causes' => array() );
		}
		$paragraphs[] = $causes[0][2];
		foreach ( array_slice( $causes, 1, 3 ) as $c ) {
			$paragraphs[] = 'Also - ' . $c[0] . ': ' . $c[2];
		}
		return array( 'headline' => 'Main problem: ' . $causes[0][0], 'paragraphs' => $paragraphs, 'causes' => $causes );
	}

	public static function score( $findings ) {
		$total = 0;
		$weights = 0;
		foreach ( self::CATEGORIES as $key => $info ) {
			$mine = array_filter( $findings, function ( $f ) use ( $key ) { return $f['category'] === $key; } );
			if ( ! $mine ) {
				continue;
			}
			$score = 100;
			foreach ( $mine as $f ) {
				$score -= 'critical' === $f['severity'] ? 30 : ( 'warning' === $f['severity'] ? 10 : 0 );
			}
			$total   += max( 0, $score ) * $info[1];
			$weights += $info[1];
		}
		return $weights ? (int) round( $total / $weights ) : null;
	}

	public static function grade( $score ) {
		if ( null === $score ) {
			return '?';
		}
		foreach ( array( 90 => 'A', 75 => 'B', 60 => 'C', 45 => 'D', 30 => 'E' ) as $limit => $letter ) {
			if ( $score >= $limit ) {
				return $letter;
			}
		}
		return 'F';
	}

	// ----------------------------------------------------------------------------------------------------
	// Admin page HTML
	// ----------------------------------------------------------------------------------------------------

	public static function render( $r ) {
		$e     = 'esc_html';
		$grade = $r['grade'];
		ob_start();
		?>
		<div class="jjsi-card jjsi-hero" data-created="<?php echo esc_attr( $r['created'] ); ?>">
			<div class="jjsi-grade jjsi-grade-<?php echo esc_attr( strtolower( $grade ) ); ?>"><b><?php echo esc_html( $grade ); ?></b><span><?php echo esc_html( null === $r['score'] ? '?' : $r['score'] ); ?>/100</span></div>
			<div class="jjsi-hero-text">
				<h2><?php echo esc_html( $r['diagnosis']['headline'] ); ?></h2>
				<?php foreach ( $r['diagnosis']['paragraphs'] as $p ) : ?>
					<p><?php echo esc_html( $p ); ?></p>
				<?php endforeach; ?>
				<p class="jjsi-muted"><?php echo esc_html( sprintf( 'Tested %s - WordPress %s, PHP %s, %d pages, %d visits each.', date_i18n( get_option( 'date_format' ) . ' ' . get_option( 'time_format' ), $r['created'] ), $r['wp_version'], $r['php'], count( $r['pages'] ), $r['pages'] ? $r['pages'][0]['runs'] : 0 ) ); ?></p>
			</div>
		</div>

		<?php
		$total = array_sum( wp_list_pluck( $r['breakdown'], 'ms' ) );
		if ( $total > 0 ) :
			?>
			<div class="jjsi-card">
				<h2>Where the time goes</h2>
				<p class="jjsi-muted">An average page built from scratch (no page cache): about <?php echo esc_html( self::ms( $r['total_ms'] ) ); ?>.</p>
				<div class="jjsi-bar">
					<?php foreach ( $r['breakdown'] as $row ) : ?>
						<?php if ( $row['ms'] > 0 ) : ?>
							<div class="jjsi-seg jjsi-seg-<?php echo esc_attr( $row['key'] ); ?>" style="width:<?php echo esc_attr( round( $row['ms'] * 100 / $total, 2 ) ); ?>%" title="<?php echo esc_attr( $row['label'] . ': ' . self::ms( $row['ms'] ) ); ?>"></div>
						<?php endif; ?>
					<?php endforeach; ?>
				</div>
				<table class="jjsi-table">
					<?php foreach ( $r['breakdown'] as $row ) : ?>
						<tr><td><i class="jjsi-dot jjsi-seg-<?php echo esc_attr( $row['key'] ); ?>"></i><?php echo esc_html( $row['label'] ); ?></td><td class="num"><?php echo esc_html( self::ms( $row['ms'] ) ); ?></td></tr>
					<?php endforeach; ?>
				</table>
			</div>
		<?php endif; ?>

		<?php
		$fixes = array_values( array_filter( $r['findings'], function ( $f ) { return in_array( $f['severity'], array( 'critical', 'warning' ), true ); } ) );
		if ( $fixes ) :
			?>
			<div class="jjsi-card">
				<h2>Fix these first</h2>
				<ol class="jjsi-fixes">
					<?php foreach ( array_slice( $fixes, 0, 10 ) as $f ) : ?>
						<li><?php self::render_finding( $f ); ?></li>
					<?php endforeach; ?>
				</ol>
			</div>
		<?php endif; ?>

		<?php if ( $r['owners'] ) : ?>
			<div class="jjsi-card">
				<h2>Time per plugin</h2>
				<p class="jjsi-muted">How long each plugin (and the theme) adds to an average page that isn't cached, including its database queries and calls to other websites.</p>
				<div class="jjsi-scroll"><table class="jjsi-table jjsi-plugins">
					<thead><tr><th>Plugin / theme</th><th class="num">Per page</th><th></th><th class="num">Queries</th><th>Calls out</th></tr></thead>
					<?php
					$max = max( 1, max( wp_list_pluck( $r['owners'], 'ms' ) ) );
					foreach ( $r['owners'] as $row ) :
						if ( $row['ms'] < 1 && ! $row['queries'] ) {
							continue;
						}
						$cls = $row['ms'] >= 300 ? 'critical' : ( $row['ms'] >= 100 ? 'warning' : 'ok' );
						?>
						<tr>
							<td><?php echo esc_html( $row['label'] ); ?></td>
							<td class="num"><?php echo esc_html( self::ms( $row['ms'] ) ); ?></td>
							<td class="jjsi-barcell"><span class="jjsi-minibar jjsi-<?php echo esc_attr( $cls ); ?>" style="width:<?php echo esc_attr( max( 1, round( $row['ms'] * 100 / $max ) ) ); ?>%"></span></td>
							<td class="num"><?php echo esc_html( $row['queries'] ); ?></td>
							<td><?php echo esc_html( implode( ', ', array_unique( $row['http'] ) ) ); ?></td>
						</tr>
					<?php endforeach; ?>
				</table></div>
			</div>
		<?php endif; ?>

		<?php
		foreach ( self::CATEGORIES as $key => $info ) :
			$mine = array_filter( $r['findings'], function ( $f ) use ( $key ) { return $f['category'] === $key; } );
			if ( ! $mine ) {
				continue;
			}
			?>
			<div class="jjsi-card">
				<h2><?php echo esc_html( $info[0] ); ?></h2>
				<?php foreach ( $mine as $f ) : ?>
					<div class="jjsi-finding-wrap"><?php self::render_finding( $f ); ?></div>
				<?php endforeach; ?>
				<?php self::render_details( $key, $r ); ?>
			</div>
		<?php endforeach; ?>
		<?php
		unset( $e );
		return ob_get_clean();
	}

	private static function render_finding( $f ) {
		?>
		<div class="jjsi-finding">
			<div class="jjsi-ftitle"><span class="jjsi-pill jjsi-<?php echo esc_attr( $f['severity'] ); ?>"><?php echo esc_html( self::SEVERITY_LABEL[ $f['severity'] ] ); ?></span> <?php echo esc_html( $f['title'] ); ?></div>
			<?php if ( $f['detail'] ) : ?>
				<p><?php echo esc_html( $f['detail'] ); ?></p>
			<?php endif; ?>
			<?php if ( $f['fix'] ) : ?>
				<p class="jjsi-fix"><b>How to fix:</b> <?php echo esc_html( $f['fix'] ); ?></p>
			<?php endif; ?>
			<?php if ( $f['items'] ) : ?>
				<details><summary>Show details (<?php echo count( $f['items'] ); ?>)</summary><ul>
					<?php foreach ( $f['items'] as $item ) : ?>
						<li><?php echo esc_html( $item ); ?></li>
					<?php endforeach; ?>
				</ul></details>
			<?php endif; ?>
		</div>
		<?php
	}

	private static function render_details( $key, $r ) {
		$c = $r['checks'];
		if ( 'page' === $key && $r['pages'] ) {
			echo '<h3>Pages tested</h3><div class="jjsi-scroll"><table class="jjsi-table"><thead><tr><th>Page</th><th class="num">Built from scratch</th><th class="num">Slowest visit</th><th class="num">Normal visit</th><th class="num">Queries</th><th class="num">Memory</th></tr></thead>';
			foreach ( $r['pages'] as $p ) {
				printf(
					'<tr><td>%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%d</td><td class="num">%s MB</td></tr>',
					esc_html( $p['url'] ), esc_html( self::ms( $p['total_ms'] ) ), esc_html( self::ms( isset( $p['max_ms'] ) ? $p['max_ms'] : $p['total_ms'] ) ), esc_html( null === $p['cached_ms'] ? '-' : self::ms( $p['cached_ms'] ) ), (int) $p['queries'], esc_html( $p['memory_mb'] )
				);
			}
			echo '</table></div>';
		}
		if ( 'external' === $key && $r['http'] ) {
			echo '<h3>Calls to other websites</h3><div class="jjsi-scroll"><table class="jjsi-table"><thead><tr><th>Address</th><th>Made by</th><th class="num">Time</th><th>Answer</th></tr></thead>';
			foreach ( $r['http'] as $call ) {
				printf( '<tr><td class="jjsi-break">%s %s</td><td>%s</td><td class="num">%s</td><td>%s</td></tr>', esc_html( $call['method'] ), esc_html( substr( $call['url'], 0, 140 ) ), esc_html( $call['label'] ), esc_html( self::ms( $call['ms'] ) ), esc_html( $call['status'] ) );
			}
			echo '</table></div>';
		}
		if ( 'database' === $key ) {
			if ( ! empty( $c['autoload']['top'] ) ) {
				echo '<h3>Biggest autoloaded options</h3><div class="jjsi-scroll"><table class="jjsi-table"><thead><tr><th>Option</th><th>Probably from</th><th class="num">Size</th></tr></thead>';
				foreach ( array_slice( $c['autoload']['top'], 0, 10 ) as $o ) {
					printf( '<tr><td class="jjsi-break">%s</td><td>%s</td><td class="num">%s</td></tr>', esc_html( $o['name'] ), esc_html( $o['owner'] ? $o['owner'] : '-' ), esc_html( JJSI_Checks::bytes( $o['size'] ) ) );
				}
				echo '</table></div>';
			}
			if ( ! empty( $c['database']['tables'] ) ) {
				printf( '<h3>Biggest tables</h3><p class="jjsi-muted">Database size: %s in %d tables.</p>', esc_html( JJSI_Checks::bytes( $c['database']['bytes'] ) ), (int) $c['database']['table_count'] );
				echo '<div class="jjsi-scroll"><table class="jjsi-table"><thead><tr><th>Table</th><th>Probably from</th><th class="num">Rows</th><th class="num">Size</th></tr></thead>';
				foreach ( array_slice( $c['database']['tables'], 0, 8 ) as $t ) {
					printf( '<tr><td>%s</td><td>%s</td><td class="num">%s</td><td class="num">%s</td></tr>', esc_html( $t['name'] ), esc_html( $t['owner'] ? $t['owner'] : '-' ), esc_html( number_format_i18n( $t['rows'] ) ), esc_html( JJSI_Checks::bytes( $t['size'] ) ) );
				}
				echo '</table></div>';
			}
			if ( $r['queries'] ) {
				echo '<h3>Slowest queries during page loads</h3><div class="jjsi-scroll"><table class="jjsi-table"><thead><tr><th class="num">Time</th><th>Run by</th><th>Query</th></tr></thead>';
				foreach ( array_slice( $r['queries'], 0, 10 ) as $q ) {
					printf( '<tr><td class="num">%s</td><td>%s</td><td class="jjsi-break jjsi-code">%s</td></tr>', esc_html( self::ms( $q['max_ms'] ) ), esc_html( self::owner_label( $q['owner'] ) ), esc_html( substr( $q['sql'], 0, 220 ) ) );
				}
				echo '</table></div>';
			}
		}
		if ( 'server' === $key ) {
			$rows = array(
				'PHP'                     => isset( $c['php']['version'] ) ? $c['php']['version'] . ' (' . $c['php']['sapi'] . ')' : '',
				'Memory limit'            => isset( $c['php']['memory_limit'] ) ? $c['php']['memory_limit'] : '',
				'Web server'              => isset( $c['php']['server'] ) ? $c['php']['server'] : '',
				'Database'                => isset( $c['db_server'] ) ? $c['db_server'] : '',
				'Processor test'          => isset( $c['cpu_ms'] ) ? $c['cpu_ms'] . ' ms (a good host: about ' . JJSI_Checks::CPU_GOOD_MS . ' ms)' : '',
				'Simple database query'   => isset( $c['db_query_ms'] ) ? $c['db_query_ms'] . ' ms' : '',
				'OPcache'                 => isset( $c['opcache'] ) ? ( $c['opcache'] ? 'on' . ( isset( $c['opcache']['hit_rate'] ) ? ', ' . $c['opcache']['hit_rate'] . '% hit rate' : '' ) : 'off' ) : '',
			);
			echo '<h3>Server details</h3><table class="jjsi-table jjsi-kv">';
			foreach ( $rows as $label => $value ) {
				if ( '' !== $value ) {
					printf( '<tr><td>%s</td><td>%s</td></tr>', esc_html( $label ), esc_html( $value ) );
				}
			}
			echo '</table>';
		}
	}

	// ----------------------------------------------------------------------------------------------------
	// Export for AI
	// ----------------------------------------------------------------------------------------------------

	/**
	 * Everything in one text file that can be handed to an AI assistant (or a developer) for analysis.
	 *
	 * @param array $r    The report.
	 * @param array $raw  The raw profiler results (url => runs), if still available.
	 */
	public static function export( $r, $raw = array() ) {
		if ( ! function_exists( 'get_plugins' ) ) {
			require_once ABSPATH . 'wp-admin/includes/plugin.php';
		}
		$lines   = array();
		$lines[] = '# Speed Inspector log export';
		$lines[] = '';
		$lines[] = 'Site: ' . $r['site'];
		$lines[] = 'Tested: ' . gmdate( 'Y-m-d H:i', $r['created'] ) . ' UTC';
		$lines[] = 'WordPress ' . $r['wp_version'] . ', PHP ' . $r['php'] . ', Speed Inspector ' . $r['version'];
		$lines[] = '';
		$lines[] = '## About this file (for whoever analyses it)';
		$lines[] = '';
		$lines[] = 'This is a performance log exported by the Speed Inspector WordPress plugin. It profiled the site\'s own pages';
		$lines[] = 'from inside WordPress as a logged-out visitor, with the page cache bypassed, several times each (medians are used).';
		$lines[] = '- All times are milliseconds unless marked. "Built from scratch" = uncached PHP page generation time.';
		$lines[] = '- "Normal visit" = an ordinary request from the server to itself, which a page cache can answer.';
		$lines[] = '- Plugin time = time loading its files + exclusive time in its hook callbacks (actions/filters), and includes';
		$lines[] = '  database queries and HTTP calls made from its code. Owners look like plugin:<folder>, theme:<folder>, core.';
		$lines[] = '- Database queries and calls to other websites are attributed to the first plugin/theme file in the call stack.';
		$lines[] = '- Only front-end pages were profiled; admin-only plugins may show little or no time.';
		$lines[] = 'Task: find the root causes of slowness and give specific, prioritised fixes (which plugin/setting, what to change).';
		$lines[] = '';
		$lines[] = '## Summary';
		$lines[] = '';
		$lines[] = 'Grade: ' . $r['grade'] . ' (' . $r['score'] . '/100)';
		$lines[] = $r['diagnosis']['headline'];
		foreach ( $r['diagnosis']['paragraphs'] as $p ) {
			$lines[] = '- ' . $p;
		}
		$lines[] = '';
		$lines[] = '## Where the time goes (average uncached page, ' . $r['total_ms'] . ' ms)';
		$lines[] = '';
		foreach ( $r['breakdown'] as $row ) {
			$lines[] = sprintf( '- %s: %d ms', $row['label'], $row['ms'] );
		}
		$lines[] = '';
		$lines[] = '## Findings (' . count( $r['findings'] ) . ')';
		foreach ( $r['findings'] as $f ) {
			$lines[] = '';
			$lines[] = sprintf( '### [%s] (%s) %s', strtoupper( $f['severity'] ), $f['category'], $f['title'] );
			if ( $f['detail'] ) {
				$lines[] = $f['detail'];
			}
			if ( $f['fix'] ) {
				$lines[] = 'Suggested fix: ' . $f['fix'];
			}
			foreach ( $f['items'] as $item ) {
				$lines[] = '- ' . $item;
			}
		}
		$lines[] = '';
		$lines[] = '## Pages tested';
		$lines[] = '';
		$base = isset( $r['baseline'] ) ? $r['baseline'] : array( 'median' => null, 'max' => null, 'errors' => 0 );
		if ( null !== $base['median'] ) {
			$lines[] = sprintf( 'Server reaching its own website (tiny static file, from the server): median %d ms, max %d ms. "Normal visit" times below include this; the cache verdict subtracts the median.', $base['median'], $base['max'] );
		}
		if ( ! empty( $r['cron_spawns']['count'] ) ) {
			$lines[] = sprintf( 'WP-Cron was started during %d of %d test visits (wp-cron.php called on this site), the longest wait was %d ms.', $r['cron_spawns']['count'], $r['run_count'], $r['cron_spawns']['max_ms'] );
		}
		if ( ! empty( $r['rewrite_runs'] ) ) {
			$lines[] = sprintf( 'Rewrite rules were rebuilt during %d of %d test visits.', $r['rewrite_runs'], $r['run_count'] );
		}
		$lines[] = '';
		$lines[] = '| Page | Built from scratch, median (ms) | Slowest visit (ms) | Normal visit (ms) | Start-up (ms) | Template (ms) | Queries | Query time (ms) | Waiting for other sites (ms) | Peak memory (MB) | HTTP status |';
		$lines[] = '|---|---|---|---|---|---|---|---|---|---|---|';
		foreach ( $r['pages'] as $p ) {
			$lines[] = sprintf( '| %s | %d | %d | %s | %d | %d | %d | %d | %d | %s | %d |', $p['url'], $p['total_ms'], isset( $p['max_ms'] ) ? $p['max_ms'] : $p['total_ms'], null === $p['cached_ms'] ? '-' : (int) $p['cached_ms'], $p['startup_ms'], $p['template_ms'], $p['queries'], $p['query_ms'], $p['http_ms'], $p['memory_mb'], $p['status'] );
		}
		$lines[] = '';
		$lines[] = '## Time per plugin / theme (average per page)';
		$lines[] = '';
		$lines[] = '| Owner | Name | ms per page | load ms (max) | Queries | Query ms | Calls to | Busiest hooks (max ms) |';
		$lines[] = '|---|---|---|---|---|---|---|---|';
		foreach ( $r['owners'] as $row ) {
			arsort( $row['hooks'] );
			$hooks = array();
			foreach ( array_slice( $row['hooks'], 0, 5, true ) as $hook => $ms ) {
				$hooks[] = $hook . ' ' . $ms;
			}
			$lines[] = sprintf( '| %s | %s | %s | %s | %d | %s | %s | %s |', $row['owner'], $row['label'], $row['ms'], $row['load_ms'], $row['queries'], $row['query_ms'], implode( ', ', array_unique( $row['http'] ) ), implode( '; ', $hooks ) );
		}
		$lines[] = '';
		$lines[] = '## Calls to other websites during page loads';
		$lines[] = '';
		if ( $r['http'] ) {
			foreach ( $r['http'] as $call ) {
				$lines[] = sprintf( '- %s %s | %s ms | by %s (%s) | status %s | on %d page(s)', $call['method'], $call['url'], $call['ms'], $call['label'], $call['owner'], $call['status'], $call['pages'] );
			}
		} else {
			$lines[] = 'None.';
		}
		$lines[] = '';
		$lines[] = '## Slowest database queries (longest single run, deduplicated)';
		$lines[] = '';
		foreach ( $r['queries'] as $q ) {
			$lines[] = sprintf( '- %s ms | x%d | %s | caller %s | %s', $q['max_ms'], $q['count'], $q['owner'], $q['caller'], $q['sql'] );
		}
		$lines[] = '';
		$lines[] = '## PHP warnings and errors during page loads';
		$lines[] = '';
		if ( $r['errors'] ) {
			foreach ( $r['errors'] as $e ) {
				$lines[] = sprintf( '- %s: %s | %s | %s | x%d', $e['type'], $e['message'], $e['file'], $e['owner'], $e['count'] );
			}
		} else {
			$lines[] = 'None.';
		}
		$lines[] = '';
		$lines[] = '## Active plugins';
		$lines[] = '';
		$all     = get_plugins();
		$active  = (array) get_option( 'active_plugins', array() );
		$updates = get_site_transient( 'update_plugins' );
		foreach ( $active as $file ) {
			if ( isset( $all[ $file ] ) ) {
				$lines[] = sprintf( '- %s %s (%s)%s', $all[ $file ]['Name'], $all[ $file ]['Version'], $file, isset( $updates->response[ $file ] ) ? ' - update available: ' . $updates->response[ $file ]->new_version : '' );
			}
		}
		$mu = function_exists( 'get_mu_plugins' ) ? get_mu_plugins() : array();
		foreach ( $mu as $file => $info ) {
			$lines[] = sprintf( '- [must-use] %s %s (%s)', $info['Name'], $info['Version'], $file );
		}
		foreach ( array( 'advanced-cache.php', 'object-cache.php', 'db.php' ) as $dropin ) {
			if ( file_exists( WP_CONTENT_DIR . '/' . $dropin ) ) {
				$lines[] = '- [drop-in] ' . $dropin;
			}
		}
		$theme   = wp_get_theme();
		$lines[] = '';
		$lines[] = 'Theme: ' . $theme->get( 'Name' ) . ' ' . $theme->get( 'Version' ) . ( $theme->parent() ? ' (child of ' . $theme->parent()->get( 'Name' ) . ' ' . $theme->parent()->get( 'Version' ) . ')' : '' );
		$lines[] = '';
		$lines[] = '## Settings';
		$lines[] = '';
		foreach ( array( 'WP_CACHE', 'WP_DEBUG', 'WP_DEBUG_LOG', 'WP_DEBUG_DISPLAY', 'SCRIPT_DEBUG', 'DISABLE_WP_CRON', 'WP_MEMORY_LIMIT', 'WP_MAX_MEMORY_LIMIT', 'WP_POST_REVISIONS', 'EMPTY_TRASH_DAYS', 'WP_ENVIRONMENT_TYPE', 'CONCATENATE_SCRIPTS' ) as $constant ) {
			$lines[] = '- ' . $constant . ': ' . ( defined( $constant ) ? var_export( constant( $constant ), true ) : '(not set)' ); // phpcs:ignore WordPress.PHP.DevelopmentFunctions
		}
		$lines[] = '- Multisite: ' . ( is_multisite() ? 'yes' : 'no' );
		$lines[] = '- Permalinks: ' . ( get_option( 'permalink_structure' ) ? get_option( 'permalink_structure' ) : 'plain' );
		$lines[] = '- Search engines discouraged: ' . ( get_option( 'blog_public' ) ? 'no' : 'yes' );
		$lines[] = '';
		$lines[] = '## Server, database, caching and cron checks (JSON)';
		$lines[] = '';
		$lines[] = '```json';
		$checks  = $r['checks'];
		unset( $checks['db_host'] );
		$lines[] = wp_json_encode( $checks, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES );
		$lines[] = '```';
		if ( $raw ) {
			$lines[] = '';
			$lines[] = '## Raw measurements from every test visit (JSON)';
			$lines[] = '';
			$lines[] = 'Per visit: total_ms, startup_ms (before plugins), template_ms, phases (ms since the request started), owners (load_ms, hooks_ms, top_hooks), the 60 slowest queries, every HTTP call, PHP errors.';
			$lines[] = '';
			$lines[] = '```json';
			$trimmed = array();
			foreach ( $raw as $url => $runs ) {
				foreach ( (array) $runs as $run ) {
					if ( ! $run ) {
						continue;
					}
					$q = (array) $run['queries'];
					usort( $q, function ( $a, $b ) { return $b['ms'] <=> $a['ms']; } );
					$run['query_time_ms'] = round( array_sum( wp_list_pluck( $q, 'ms' ) ), 2 );
					$run['queries']       = array_slice( $q, 0, 60 );
					$run['url']           = self::clean_url( $run['url'] );
					unset( $run['run'] );
					$trimmed[ self::clean_url( $url ) ][] = $run;
				}
			}
			$lines[] = wp_json_encode( $trimmed, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES );
			$lines[] = '```';
		}
		$lines[] = '';
		$lines[] = '(Database passwords, keys and user data are not included. Query text is included and could contain content from your site.)';
		// Other plugins sometimes log the test addresses - never let the one-time key end up in the file
		return preg_replace( '/(jjsi_profile=)[A-Za-z0-9%]+/', '$1[removed]', implode( "\n", $lines ) ) . "\n";
	}
}
