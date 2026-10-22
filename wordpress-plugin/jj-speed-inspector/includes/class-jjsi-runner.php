<?php
/**
 * Runs a test: chooses pages, visits each one normally (to time the page cache) and with the profiler on (to see
 * what WordPress does), runs the server checks, then builds and saves the report.
 *
 * The admin page drives it one small step at a time over AJAX, so no single request runs for long.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_Runner {
	const RUNS_PER_PAGE   = 3;
	const VISITS_PER_PAGE = 2;
	const BASELINE_VISITS = 3;
	const MAX_PAGES       = 4;
	const STATE           = 'jjsi_state';
	const REPORT          = 'jjsi_last_report';
	const RAW             = 'jjsi_last_raw';

	public static function init() {
		add_action( 'wp_ajax_jjsi_step', array( __CLASS__, 'ajax' ) );
		add_action( 'admin_init', array( __CLASS__, 'keep_loader_current' ) );
	}

	public static function keep_loader_current() {
		if ( current_user_can( 'activate_plugins' ) && get_option( 'jjsi_loader_version' ) !== JJSI_VERSION ) {
			JJSI_Loader::install();
			update_option( 'jjsi_loader_version', JJSI_VERSION, false );
		}
	}

	/** The pages to test: home, the newest post, a page, the shop - plus one the user asked for. */
	public static function pages( $extra = '' ) {
		$urls = array( home_url( '/' ) );
		$post = get_posts( array( 'numberposts' => 1, 'post_type' => 'post', 'post_status' => 'publish', 'fields' => 'ids' ) );
		if ( $post ) {
			$urls[] = get_permalink( $post[0] );
		}
		$exclude = array_filter( array( (int) get_option( 'page_on_front' ), (int) get_option( 'page_for_posts' ) ) );
		$page    = get_posts( array( 'numberposts' => 1, 'post_type' => 'page', 'post_status' => 'publish', 'fields' => 'ids', 'exclude' => $exclude, 'orderby' => 'menu_order date', 'order' => 'ASC' ) );
		if ( $page ) {
			$urls[] = get_permalink( $page[0] );
		}
		if ( function_exists( 'wc_get_page_permalink' ) ) {
			$shop = wc_get_page_permalink( 'shop' );
			if ( $shop && false === strpos( $shop, '?' ) ) {
				$urls[] = $shop;
			}
		}
		if ( $extra ) {
			$extra = esc_url_raw( trim( $extra ) );
			if ( $extra && 0 === strpos( $extra, home_url() ) ) {
				array_unshift( $urls, $extra );
			}
		}
		return array_slice( array_values( array_unique( $urls ) ), 0, self::MAX_PAGES + ( $extra ? 1 : 0 ) );
	}

	/** Start a test: create the one-time key and the list of steps. */
	public static function begin( $extra = '' ) {
		$token = wp_generate_password( 40, false );
		set_transient( 'jjsi_token', $token, HOUR_IN_SECONDS );
		$urls  = self::pages( $extra );
		$steps = array();
		for ( $i = 0; $i < self::BASELINE_VISITS; $i++ ) {
			$steps[] = array( 'do' => 'baseline' );
		}
		foreach ( $urls as $url ) {
			for ( $i = 0; $i < self::VISITS_PER_PAGE; $i++ ) {
				$steps[] = array( 'do' => 'visit', 'url' => $url );
			}
			for ( $i = 0; $i < self::RUNS_PER_PAGE; $i++ ) {
				$steps[] = array( 'do' => 'profile', 'url' => $url );
			}
		}
		$steps[] = array( 'do' => 'checks' );
		$steps[] = array( 'do' => 'finish' );
		$state = array( 'token' => $token, 'urls' => $urls, 'runs' => array(), 'visits' => array(), 'baseline' => array(), 'notes' => array(), 'checks' => null, 'started' => time() );
		update_option( self::STATE, $state, false );
		return array( 'steps' => $steps, 'urls' => $urls );
	}

	private static function state() {
		$state = get_option( self::STATE );
		return is_array( $state ) ? $state : null;
	}

	private static function save_state( $state ) {
		update_option( self::STATE, $state, false );
	}

	private static function request_args() {
		return array(
			'timeout'     => 60,
			'redirection' => 3,
			'sslverify'   => apply_filters( 'https_local_ssl_verify', false ), // same as WordPress's own loopback tests
			'user-agent'  => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 SpeedInspector/' . JJSI_VERSION,
			'headers'     => array( 'Accept' => 'text/html', 'Cache-Control' => 'no-cache' ),
			'cookies'     => array(),
		);
	}

	/**
	 * How long the server takes just to fetch a tiny static file from itself (no PHP involved). This is the cost of
	 * the server connecting to its own website - it's included in every "normal visit" timing, and it's what
	 * WP-Cron and WordPress's other self-requests pay.
	 */
	public static function baseline() {
		$state = self::state();
		$start = microtime( true );
		$resp  = wp_remote_get( add_query_arg( 'jjsi', wp_rand(), includes_url( 'images/blank.gif' ) ), self::request_args() );
		$ms    = ( microtime( true ) - $start ) * 1000;
		$entry = is_wp_error( $resp ) ? array( 'error' => $resp->get_error_message(), 'ms' => round( $ms, 1 ) ) : array( 'ms' => round( $ms, 1 ), 'status' => wp_remote_retrieve_response_code( $resp ) );
		if ( $state ) {
			$state['baseline'][] = $entry;
			self::save_state( $state );
		}
		return array( 'ok' => ! is_wp_error( $resp ) ) + $entry;
	}

	/** A normal visit, as a page cache would see it. */
	public static function visit( $url ) {
		$state = self::state();
		$start = microtime( true );
		$resp  = wp_remote_get( $url, self::request_args() );
		$ms    = ( microtime( true ) - $start ) * 1000;
		if ( is_wp_error( $resp ) ) {
			return array( 'ok' => false, 'error' => $resp->get_error_message() );
		}
		$cache = '';
		foreach ( array( 'x-cache', 'cf-cache-status', 'x-litespeed-cache', 'x-proxy-cache', 'x-wp-super-cache', 'x-rocket-nginx-serving-static', 'x-sg-cache', 'x-kinsta-cache', 'x-cache-status', 'age' ) as $header ) {
			$value = wp_remote_retrieve_header( $resp, $header );
			if ( $value ) {
				$cache = $header . ': ' . ( is_array( $value ) ? implode( ', ', $value ) : $value );
				break;
			}
		}
		$entry = array( 'ms' => round( $ms, 1 ), 'status' => wp_remote_retrieve_response_code( $resp ), 'cache' => $cache, 'bytes' => strlen( wp_remote_retrieve_body( $resp ) ) );
		if ( $state ) {
			$state['visits'][ $url ][] = $entry;
			self::save_state( $state );
		}
		return array( 'ok' => true ) + $entry;
	}

	/** A normal visit timed by the browser (when the server can't call itself). */
	public static function record_visit( $url, $ms, $status ) {
		$state = self::state();
		if ( $state ) {
			$state['visits'][ $url ][] = array( 'ms' => round( (float) $ms, 1 ), 'status' => (int) $status, 'cache' => '', 'bytes' => 0, 'by_browser' => true );
			self::save_state( $state );
		}
		return array( 'ok' => true );
	}

	public static function profile_url( $url, $run ) {
		$state = self::state();
		return add_query_arg(
			array(
				JJSI_Profiler::PARAM_TOKEN => rawurlencode( $state['token'] ),
				JJSI_Profiler::PARAM_RUN   => $run,
				'jjsi_nocache'             => $run,
			),
			$url
		);
	}

	/** A visit with the profiler on. If the server can't call itself, the admin page does it in a hidden frame. */
	public static function profile( $url ) {
		$run  = strtolower( wp_generate_password( 16, false ) );
		$resp = wp_remote_get( self::profile_url( $url, $run ), self::request_args() );
		$data = get_transient( 'jjsi_result_' . $run );
		if ( ! $data ) {
			$why = is_wp_error( $resp ) ? $resp->get_error_message() : 'HTTP ' . wp_remote_retrieve_response_code( $resp );
			return array( 'ok' => false, 'run' => $run, 'url' => $url, 'frame_url' => self::profile_url( $url, $run ), 'error' => $why );
		}
		return self::store( $url, $run, $data );
	}

	/** Pick up the result of a visit made by the browser instead of the server. */
	public static function collect( $url, $run ) {
		$data = get_transient( 'jjsi_result_' . preg_replace( '/[^a-z0-9]/', '', strtolower( $run ) ) );
		if ( ! $data ) {
			return array( 'ok' => false );
		}
		return self::store( $url, $run, $data );
	}

	private static function store( $url, $run, $data ) {
		delete_transient( 'jjsi_result_' . $run );
		$state = self::state();
		if ( $state ) {
			$state['runs'][ $url ][] = $data;
			self::save_state( $state );
		}
		return array( 'ok' => true, 'total_ms' => $data['total_ms'], 'queries' => $data['query_count'] );
	}

	public static function note( $text ) {
		$state = self::state();
		if ( $state ) {
			$state['notes'][] = $text;
			self::save_state( $state );
		}
	}

	public static function checks() {
		$state = self::state();
		$state['checks'] = JJSI_Checks::run();
		self::save_state( $state );
		return array( 'ok' => true );
	}

	public static function finish() {
		$state = self::state();
		if ( ! $state ) {
			return null;
		}
		if ( null === $state['checks'] ) {
			$state['checks'] = JJSI_Checks::run();
		}
		$report = JJSI_Report::build( $state['runs'], $state['visits'], $state['checks'], array_unique( $state['notes'] ), isset( $state['baseline'] ) ? $state['baseline'] : array() );
		update_option( self::REPORT, $report, false );
		update_option( self::RAW, $state['runs'], false );
		delete_option( self::STATE );
		delete_transient( 'jjsi_token' );
		return $report;
	}

	/** Run a whole test in one go (WP-CLI). */
	public static function run_all( $extra = '', $progress = null ) {
		$plan = self::begin( $extra );
		foreach ( $plan['steps'] as $i => $step ) {
			if ( $progress ) {
				call_user_func( $progress, $i + 1, count( $plan['steps'] ), $step );
			}
			if ( 'baseline' === $step['do'] ) {
				self::baseline();
			} elseif ( 'visit' === $step['do'] ) {
				$r = self::visit( $step['url'] );
				if ( empty( $r['ok'] ) ) {
					self::note( 'Normal visit to ' . $step['url'] . ' failed: ' . $r['error'] );
				}
			} elseif ( 'profile' === $step['do'] ) {
				$r = self::profile( $step['url'] );
				if ( empty( $r['ok'] ) ) {
					self::note( 'Test visit to ' . $step['url'] . ' failed: ' . $r['error'] );
				}
			} elseif ( 'checks' === $step['do'] ) {
				self::checks();
			}
		}
		return self::finish();
	}

	public static function ajax() {
		check_ajax_referer( 'jjsi', 'nonce' );
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_send_json_error( 'Not allowed', 403 );
		}
		if ( function_exists( 'set_time_limit' ) ) {
			@set_time_limit( 120 ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
		}
		$do  = isset( $_POST['do'] ) ? sanitize_key( wp_unslash( $_POST['do'] ) ) : '';
		$url = isset( $_POST['url'] ) ? esc_url_raw( wp_unslash( $_POST['url'] ) ) : '';
		if ( $url && 0 !== strpos( $url, home_url() ) ) {
			wp_send_json_error( 'Only this site\'s own pages can be tested' );
		}
		try {
			switch ( $do ) {
				case 'begin':
					$extra = isset( $_POST['extra'] ) ? esc_url_raw( wp_unslash( $_POST['extra'] ) ) : '';
					wp_send_json_success( self::begin( $extra ) );
					break;
				case 'baseline':
					wp_send_json_success( self::baseline() );
					break;
				case 'visit':
					wp_send_json_success( self::visit( $url ) );
					break;
				case 'profile':
					wp_send_json_success( self::profile( $url ) );
					break;
				case 'record_visit':
					wp_send_json_success( self::record_visit( $url, isset( $_POST['ms'] ) ? (float) $_POST['ms'] : 0, isset( $_POST['status'] ) ? (int) $_POST['status'] : 0 ) );
					break;
				case 'collect':
					$run = isset( $_POST['run'] ) ? sanitize_key( wp_unslash( $_POST['run'] ) ) : '';
					wp_send_json_success( self::collect( $url, $run ) );
					break;
				case 'note':
					self::note( isset( $_POST['text'] ) ? sanitize_text_field( wp_unslash( $_POST['text'] ) ) : '' );
					wp_send_json_success();
					break;
				case 'checks':
					wp_send_json_success( self::checks() );
					break;
				case 'finish':
					$report = self::finish();
					wp_send_json_success( array( 'html' => $report ? JJSI_Report::render( $report ) : '' ) );
					break;
			}
		} catch ( Throwable $e ) {
			wp_send_json_error( $e->getMessage() . ' (' . basename( $e->getFile() ) . ':' . $e->getLine() . ')' );
		}
		wp_send_json_error( 'Unknown step' );
	}
}
