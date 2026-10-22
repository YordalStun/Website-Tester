<?php
/**
 * The profiler. It only ever switches on for a Speed Inspector test request (one that carries the secret
 * one-time key) - normal visitors are never profiled.
 *
 * It times:
 *  - loading each plugin's main file
 *  - every hook callback (action/filter), attributed to the plugin/theme whose file it lives in
 *  - every database query, attributed to the plugin/theme that ran it
 *  - every call to another website (WordPress HTTP API)
 *  - PHP warnings/notices, and which plugin caused them
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

if ( class_exists( 'JJSI_Profiler' ) ) {
	return;
}

class JJSI_Profiler {
	const PARAM_TOKEN = 'jjsi_profile';
	const PARAM_RUN   = 'jjsi_run';

	public static $active = false;
	public static $early  = false; // started from the must-use plugin (so plugin loading is measured too)

	private static $run_id      = '';
	private static $start_ns    = 0;
	private static $request_t0  = 0.0;
	private static $last_mark   = 0;
	private static $stack       = array();
	private static $hook_counts = array();
	private static $owners      = array();
	private static $owner_cache = array();
	private static $plugin_load = array();
	private static $phases      = array();
	private static $queries     = array();
	private static $http        = array();
	private static $http_open   = array();
	private static $errors      = array();
	private static $callback_ns = 0;
	private static $template_at = null;
	private static $self_dir    = '';
	private static $prev_error_handler = null;
	private static $linked = null;
	private static $rewrite_rebuilt = 0;

	/**
	 * Is this request a valid Speed Inspector test? Checks the one-time key.
	 */
	public static function is_test_request() {
		if ( empty( $_GET[ self::PARAM_TOKEN ] ) || empty( $_GET[ self::PARAM_RUN ] ) ) { // phpcs:ignore WordPress.Security.NonceVerification
			return false;
		}
		$given    = (string) wp_unslash( $_GET[ self::PARAM_TOKEN ] ); // phpcs:ignore WordPress.Security.NonceVerification,WordPress.Security.ValidatedSanitizedInput
		$expected = get_transient( 'jjsi_token' );
		return is_string( $expected ) && '' !== $expected && hash_equals( $expected, $given );
	}

	public static function maybe_start( $early ) {
		if ( self::$active || ! self::is_test_request() ) {
			return;
		}
		self::start( $early );
	}

	private static function start( $early ) {
		self::$active     = true;
		self::$early      = $early;
		self::$run_id     = preg_replace( '/[^a-zA-Z0-9_-]/', '', (string) wp_unslash( $_GET[ self::PARAM_RUN ] ) ); // phpcs:ignore WordPress.Security.NonceVerification,WordPress.Security.ValidatedSanitizedInput
		self::$start_ns   = hrtime( true );
		self::$last_mark  = self::$start_ns;
		self::$request_t0 = isset( $_SERVER['REQUEST_TIME_FLOAT'] ) ? (float) $_SERVER['REQUEST_TIME_FLOAT'] : microtime( true );
		self::$self_dir   = wp_normalize_path( dirname( __DIR__ ) ) . '/';
		self::$phases['profiler_start'] = self::since_request();

		// Make sure no page cache stores or serves this test request.
		if ( ! defined( 'DONOTCACHEPAGE' ) ) {
			define( 'DONOTCACHEPAGE', true );
		}
		if ( ! defined( 'SAVEQUERIES' ) ) {
			define( 'SAVEQUERIES', true );
		}

		add_action( 'all', array( __CLASS__, 'on_all' ) );
		add_action( 'plugin_loaded', array( __CLASS__, 'on_plugin_loaded' ) );
		add_action( 'mu_plugin_loaded', array( __CLASS__, 'on_mu_plugin_loaded' ) );
		add_filter( 'log_query_custom_data', array( __CLASS__, 'on_query' ), 10, 5 );
		add_filter( 'pre_http_request', array( __CLASS__, 'on_http_start' ), PHP_INT_MAX, 3 );
		add_action( 'http_api_debug', array( __CLASS__, 'on_http_end' ), 10, 5 );
		add_filter( 'template_include', array( __CLASS__, 'on_template' ), PHP_INT_MAX );
		add_filter( 'rewrite_rules_array', array( __CLASS__, 'on_rewrite_rules' ), PHP_INT_MAX );
		add_action( 'shutdown', array( __CLASS__, 'finish' ), PHP_INT_MAX );
		foreach ( array( 'muplugins_loaded', 'plugins_loaded', 'setup_theme', 'after_setup_theme', 'init', 'wp_loaded', 'wp', 'template_redirect', 'wp_head', 'wp_footer' ) as $phase ) {
			add_action( $phase, array( __CLASS__, 'on_phase' ), PHP_INT_MIN );
		}
		self::$prev_error_handler = set_error_handler( array( __CLASS__, 'on_error' ) ); // phpcs:ignore WordPress.PHP.DevelopmentFunctions
		nocache_headers();
	}

	private static function since_request() {
		return round( ( microtime( true ) - self::$request_t0 ) * 1000, 2 );
	}

	public static function on_phase() {
		$hook = current_filter();
		if ( ! isset( self::$phases[ $hook ] ) ) {
			self::$phases[ $hook ] = self::since_request();
		}
		if ( 'muplugins_loaded' === $hook ) {
			self::$last_mark = hrtime( true );
		}
	}

	// ------------------------------------------------------------------------------------------------
	// Plugin loading
	// ------------------------------------------------------------------------------------------------

	public static function on_plugin_loaded( $file ) {
		self::record_load( 'plugin', $file );
	}

	public static function on_mu_plugin_loaded( $file ) {
		self::record_load( 'mu', $file );
	}

	private static function record_load( $kind, $file ) {
		$now   = hrtime( true );
		$owner = self::owner_of_file( $file );
		if ( 'self' !== $owner ) {
			self::$plugin_load[ $owner ] = ( isset( self::$plugin_load[ $owner ] ) ? self::$plugin_load[ $owner ] : 0 ) + ( $now - self::$last_mark );
		}
		self::$last_mark = $now;
	}

	// ------------------------------------------------------------------------------------------------
	// Hook callbacks: wrap each one in a timer the first time its hook runs
	// ------------------------------------------------------------------------------------------------

	public static function on_all( $hook ) {
		global $wp_filter;
		if ( ! self::$active || 'all' === $hook || ! isset( $wp_filter[ $hook ] ) || ! ( $wp_filter[ $hook ] instanceof WP_Hook ) ) {
			return;
		}
		$wp_hook = $wp_filter[ $hook ];
		$count   = 0;
		foreach ( $wp_hook->callbacks as $callbacks ) {
			$count += count( $callbacks );
		}
		if ( isset( self::$hook_counts[ $hook ] ) && self::$hook_counts[ $hook ] === $count ) {
			return; // nothing new since we last wrapped this hook
		}
		self::$hook_counts[ $hook ] = $count;
		foreach ( $wp_hook->callbacks as $priority => $callbacks ) {
			foreach ( $callbacks as $key => $callback ) {
				if ( ! empty( $callback['jjsi'] ) ) {
					continue;
				}
				$original = $callback['function'];
				$owner    = self::owner_of_callback( $original );
				if ( 'self' === $owner ) {
					continue;
				}
				$wp_hook->callbacks[ $priority ][ $key ]['function'] = self::wrap( $original, $owner, $hook );
				$wp_hook->callbacks[ $priority ][ $key ]['jjsi']     = true;
			}
		}
	}

	private static function wrap( $original, $owner, $hook ) {
		return function () use ( $original, $owner, $hook ) {
			$args  = func_get_args();
			$start = hrtime( true );
			JJSI_Profiler::push();
			try {
				return call_user_func_array( $original, $args );
			} finally {
				JJSI_Profiler::pop( $owner, $hook, hrtime( true ) - $start );
			}
		};
	}

	/** @internal */
	public static function push() {
		self::$stack[] = 0;
	}

	/** @internal */
	public static function pop( $owner, $hook, $elapsed ) {
		$children  = (int) array_pop( self::$stack );
		$exclusive = max( 0, $elapsed - $children );
		if ( self::$stack ) {
			self::$stack[ count( self::$stack ) - 1 ] += $elapsed;
		}
		if ( ! self::$active ) {
			return;
		}
		self::$callback_ns += $exclusive;
		if ( ! isset( self::$owners[ $owner ] ) ) {
			self::$owners[ $owner ] = array( 'ns' => 0, 'calls' => 0, 'hooks' => array() );
		}
		self::$owners[ $owner ]['ns'] += $exclusive;
		self::$owners[ $owner ]['calls']++;
		if ( ! isset( self::$owners[ $owner ]['hooks'][ $hook ] ) ) {
			self::$owners[ $owner ]['hooks'][ $hook ] = 0;
		}
		self::$owners[ $owner ]['hooks'][ $hook ] += $exclusive;
	}

	// ------------------------------------------------------------------------------------------------
	// Who does this code belong to?
	// ------------------------------------------------------------------------------------------------

	public static function owner_of_callback( $callback ) {
		$key = null;
		if ( is_string( $callback ) ) {
			$key = $callback;
		} elseif ( is_array( $callback ) && isset( $callback[0], $callback[1] ) ) {
			$key = ( is_object( $callback[0] ) ? get_class( $callback[0] ) : (string) $callback[0] ) . '::' . $callback[1];
		}
		if ( null !== $key && isset( self::$owner_cache[ $key ] ) ) {
			return self::$owner_cache[ $key ];
		}
		$owner = 'core';
		try {
			if ( is_string( $callback ) && strpos( $callback, '::' ) ) {
				$callback = explode( '::', $callback, 2 );
			}
			if ( is_array( $callback ) ) {
				$reflection = new ReflectionMethod( $callback[0], $callback[1] );
			} elseif ( $callback instanceof Closure || is_string( $callback ) ) {
				$reflection = new ReflectionFunction( $callback );
			} elseif ( is_object( $callback ) && method_exists( $callback, '__invoke' ) ) {
				$reflection = new ReflectionMethod( $callback, '__invoke' );
			} else {
				$reflection = null;
			}
			$file = $reflection ? $reflection->getFileName() : false;
			if ( $file ) {
				$owner = self::owner_of_file( $file );
			}
		} catch ( Throwable $e ) {
			$owner = 'unknown';
		}
		if ( null !== $key ) {
			self::$owner_cache[ $key ] = $owner;
		}
		return $owner;
	}

	public static function owner_of_file( $file ) {
		$file = wp_normalize_path( (string) $file );
		if ( self::$self_dir && 0 === strpos( $file, self::$self_dir ) ) {
			return 'self';
		}
		if ( false !== strpos( $file, '/mu-plugins/0-jj-speed-inspector' ) ) {
			return 'self';
		}
		$places = array(
			'plugin' => wp_normalize_path( WP_PLUGIN_DIR ) . '/',
			'mu'     => wp_normalize_path( WPMU_PLUGIN_DIR ) . '/',
			'theme'  => wp_normalize_path( WP_CONTENT_DIR ) . '/themes/',
		);
		if ( function_exists( 'get_theme_root' ) ) {
			$theme_root = wp_normalize_path( get_theme_root() ) . '/';
			if ( $theme_root !== $places['theme'] ) {
				$places['theme2'] = $theme_root;
			}
		}
		foreach ( self::linked_folders() as $real => $owner ) {
			if ( 0 === strpos( $file, $real ) ) {
				return $owner;
			}
		}
		foreach ( $places as $kind => $dir ) {
			if ( 0 === strpos( $file, $dir ) ) {
				$rest = substr( $file, strlen( $dir ) );
				$slug = strtok( $rest, '/' );
				return ( 'theme2' === $kind ? 'theme' : $kind ) . ':' . $slug;
			}
		}
		$abspath = wp_normalize_path( ABSPATH );
		if ( 0 === strpos( $file, $abspath . 'wp-includes/' ) || 0 === strpos( $file, $abspath . 'wp-admin/' ) ) {
			return 'core';
		}
		if ( 0 === strpos( $file, wp_normalize_path( WP_CONTENT_DIR ) . '/' ) ) {
			return 'dropin:' . basename( $file );
		}
		return 'other';
	}

	/**
	 * Plugin/theme folders that are symlinks point somewhere else on disk, and PHP reports the real location of
	 * their files. Map those real locations back to the plugin/theme.
	 */
	private static function linked_folders() {
		if ( null !== self::$linked ) {
			return self::$linked;
		}
		self::$linked = array();
		$places = array( 'plugin' => WP_PLUGIN_DIR, 'mu' => WPMU_PLUGIN_DIR, 'theme' => WP_CONTENT_DIR . '/themes' );
		foreach ( $places as $kind => $dir ) {
			$entries = @scandir( $dir ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
			foreach ( (array) $entries as $entry ) {
				if ( '.' === $entry || '..' === $entry || ! is_link( $dir . '/' . $entry ) ) {
					continue;
				}
				$real = realpath( $dir . '/' . $entry );
				if ( $real ) {
					self::$linked[ wp_normalize_path( $real ) . ( is_dir( $real ) ? '/' : '' ) ] = $kind . ':' . $entry;
				}
			}
		}
		return self::$linked;
	}

	/** The first plugin/theme in the current call stack (for queries, HTTP calls and errors). */
	private static function owner_from_backtrace() {
		$trace = debug_backtrace( DEBUG_BACKTRACE_IGNORE_ARGS, 40 ); // phpcs:ignore WordPress.PHP.DevelopmentFunctions
		foreach ( $trace as $frame ) {
			if ( empty( $frame['file'] ) ) {
				continue;
			}
			$owner = self::owner_of_file( $frame['file'] );
			if ( 'core' !== $owner && 'self' !== $owner && 'other' !== $owner ) {
				return $owner;
			}
		}
		return 'core';
	}

	// ------------------------------------------------------------------------------------------------
	// Database, HTTP and errors
	// ------------------------------------------------------------------------------------------------

	public static function on_query( $query_data, $query, $query_time, $query_callstack, $query_start ) {
		if ( self::$active && count( self::$queries ) < 3000 ) {
			$callers = array_map( 'trim', explode( ',', (string) $query_callstack ) );
			self::$queries[] = array(
				'sql'    => substr( preg_replace( '/\s+/', ' ', (string) $query ), 0, 400 ),
				'ms'     => round( $query_time * 1000, 3 ),
				'owner'  => self::owner_from_backtrace(),
				'caller' => (string) end( $callers ),
			);
		}
		return $query_data;
	}

	public static function on_http_start( $pre, $args, $url ) {
		if ( self::$active && false === $pre ) {
			self::$http_open[] = array( 'url' => (string) $url, 'start' => hrtime( true ), 'owner' => self::owner_from_backtrace(), 'method' => isset( $args['method'] ) ? $args['method'] : 'GET' );
		}
		return $pre;
	}

	public static function on_http_end( $response, $context, $class, $args, $url ) {
		if ( ! self::$active ) {
			return;
		}
		foreach ( array_reverse( self::$http_open, true ) as $i => $open ) {
			if ( $open['url'] === $url ) {
				unset( self::$http_open[ $i ] );
				self::$http[] = array(
					'url'    => substr( (string) $url, 0, 300 ),
					'method' => $open['method'],
					'ms'     => round( ( hrtime( true ) - $open['start'] ) / 1e6, 1 ),
					'owner'  => $open['owner'],
					'status' => is_wp_error( $response ) ? $response->get_error_message() : wp_remote_retrieve_response_code( $response ),
				);
				return;
			}
		}
	}

	public static function on_error( $errno, $errstr, $errfile = '', $errline = 0 ) {
		if ( self::$active && count( self::$errors ) < 100 && ( error_reporting() & $errno ) ) {
			$key = $errfile . ':' . $errline;
			if ( ! isset( self::$errors[ $key ] ) ) {
				$types = array( E_WARNING => 'Warning', E_NOTICE => 'Notice', E_DEPRECATED => 'Deprecated', E_USER_WARNING => 'Warning', E_USER_NOTICE => 'Notice', E_USER_DEPRECATED => 'Deprecated' );
				self::$errors[ $key ] = array(
					'type'    => isset( $types[ $errno ] ) ? $types[ $errno ] : 'Error',
					'message' => substr( (string) $errstr, 0, 300 ),
					'file'    => str_replace( wp_normalize_path( ABSPATH ), '', wp_normalize_path( (string) $errfile ) ) . ':' . $errline,
					'owner'   => self::error_owner( $errfile ),
					'count'   => 0,
				);
			}
			self::$errors[ $key ]['count']++;
		}
		if ( self::$prev_error_handler ) {
			return call_user_func( self::$prev_error_handler, $errno, $errstr, $errfile, $errline );
		}
		return false; // let PHP handle it as normal
	}

	/**
	 * The rewrite rules (permalinks) were rebuilt during this page load. Normal page loads read them from the
	 * database; rebuilding should only happen when permalink settings change.
	 */
	public static function on_rewrite_rules( $rules ) {
		self::$rewrite_rebuilt++;
		return $rules;
	}

	/**
	 * Warnings raised inside WordPress's own files are almost always caused by a plugin or theme passing it bad
	 * data, so blame the first plugin/theme in the call stack.
	 */
	private static function error_owner( $errfile ) {
		$owner = self::owner_of_file( $errfile );
		if ( 'core' === $owner ) {
			$caller = self::owner_from_backtrace();
			if ( 'core' !== $caller ) {
				return $caller;
			}
		}
		return $owner;
	}

	public static function on_template( $template ) {
		self::$template_at = array( hrtime( true ), self::$callback_ns );
		self::$phases['template'] = self::since_request();
		if ( $template ) {
			$owner = self::owner_of_file( $template );
			self::$phases['template_owner'] = $owner;
		}
		return $template;
	}

	// ------------------------------------------------------------------------------------------------
	// End of the request: save what we measured for the Speed Inspector page to read
	// ------------------------------------------------------------------------------------------------

	public static function finish() {
		if ( ! self::$active ) {
			return;
		}
		self::$active = false;
		remove_action( 'all', array( __CLASS__, 'on_all' ) );
		$end_ns   = hrtime( true );
		$total_ms = round( ( microtime( true ) - self::$request_t0 ) * 1000, 2 );

		$owners = array();
		foreach ( self::$owners as $owner => $info ) {
			arsort( $info['hooks'] );
			$owners[ $owner ] = array(
				'hooks_ms' => round( $info['ns'] / 1e6, 2 ),
				'load_ms'  => 0,
				'calls'    => $info['calls'],
				'top_hooks' => array_map(
					function ( $ns ) {
						return round( $ns / 1e6, 2 );
					},
					array_slice( $info['hooks'], 0, 6, true )
				),
			);
		}
		foreach ( self::$plugin_load as $owner => $ns ) {
			if ( ! isset( $owners[ $owner ] ) ) {
				$owners[ $owner ] = array( 'hooks_ms' => 0, 'load_ms' => 0, 'calls' => 0, 'top_hooks' => array() );
			}
			$owners[ $owner ]['load_ms'] = round( $ns / 1e6, 2 );
		}

		$template_ms = 0;
		if ( self::$template_at ) {
			list( $at, $callbacks_before ) = self::$template_at;
			$template_ms = max( 0, ( $end_ns - $at - ( self::$callback_ns - $callbacks_before ) ) / 1e6 );
		}

		global $wpdb;
		$result = array(
			'version'     => 1,
			'run'         => self::$run_id,
			'url'         => ( is_ssl() ? 'https://' : 'http://' ) . ( isset( $_SERVER['HTTP_HOST'] ) ? sanitize_text_field( wp_unslash( $_SERVER['HTTP_HOST'] ) ) : '' ) . ( isset( $_SERVER['REQUEST_URI'] ) ? esc_url_raw( wp_unslash( $_SERVER['REQUEST_URI'] ) ) : '' ),
			'early'       => self::$early,
			'total_ms'    => $total_ms,
			'startup_ms'  => isset( self::$phases['profiler_start'] ) ? self::$phases['profiler_start'] : 0,
			'template_ms' => round( $template_ms, 2 ),
			'template_owner' => isset( self::$phases['template_owner'] ) ? self::$phases['template_owner'] : '',
			'phases'      => self::$phases,
			'owners'      => $owners,
			'queries'     => self::$queries,
			'query_count' => isset( $wpdb->num_queries ) ? (int) $wpdb->num_queries : count( self::$queries ),
			'http'        => self::$http,
			'errors'      => array_values( self::$errors ),
			'memory_mb'   => round( memory_get_peak_usage( true ) / 1048576, 1 ),
			'status'      => http_response_code(),
			'object_cache' => wp_using_ext_object_cache(),
			'is_404'      => function_exists( 'is_404' ) && is_404(),
			'hooks_seen'  => count( self::$hook_counts ),
			'rewrite_rebuilt' => self::$rewrite_rebuilt,
		);
		set_transient( 'jjsi_result_' . self::$run_id, $result, 30 * MINUTE_IN_SECONDS );
	}
}
