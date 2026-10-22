<?php
/**
 * Plugin Name: Slowpoke (test plugin)
 * Description: Deliberately slow plugin used to test Speed Inspector. Never install this on a real site.
 * Version: 0.1
 */

defined( 'ABSPATH' ) || exit;

// Burns CPU time on every page.
add_action(
	'init',
	function () {
		if ( is_admin() || wp_doing_ajax() || wp_doing_cron() || ( defined( 'WP_CLI' ) && WP_CLI ) ) {
			return;
		}
		$end = microtime( true ) + 0.25;
		while ( microtime( true ) < $end ) {
			md5( (string) mt_rand() );
		}
	}
);

// Calls another website on every page, and runs a slow database query.
add_action(
	'wp_head',
	function () {
		$url = getenv( 'SLOWPOKE_REMOTE' ) ? getenv( 'SLOWPOKE_REMOTE' ) : 'http://127.0.0.1:8897/slow-api';
		wp_remote_get( $url, array( 'timeout' => 5 ) );
		global $wpdb;
		$wpdb->get_var( 'SELECT SLEEP(0.08)' );
		$undefined = array();
		echo '<!-- slowpoke ' . esc_html( (string) @$undefined['missing'] ) . ' -->'; // silenced: not reported
		$value = $undefined['missing']; // PHP warning on PHP 8, notice on 7.4
		unset( $value );
	}
);

// Rebuilds the permalinks on every page load (a common plugin/theme mistake).
add_action(
	'init',
	function () {
		if ( ! is_admin() ) {
			flush_rewrite_rules();
		}
	},
	20
);

// A background task every minute (like Action Scheduler's queue runner), so WP-Cron is due on most page loads.
add_filter(
	'cron_schedules',
	function ( $schedules ) {
		$schedules['slowpoke_every_minute'] = array( 'interval' => 60, 'display' => 'Every minute' );
		return $schedules;
	}
);
add_action(
	'init',
	function () {
		if ( ! wp_next_scheduled( 'slowpoke_every_minute_task' ) ) {
			wp_schedule_event( time(), 'slowpoke_every_minute', 'slowpoke_every_minute_task' );
		}
	}
);
add_action( 'slowpoke_every_minute_task', '__return_true' );

// Lots of autoloaded data and an overdue scheduled task.
register_activation_hook(
	__FILE__,
	function () {
		update_option( 'slowpoke_big_cache', str_repeat( 'x', 1200 * 1024 ), true );
		wp_schedule_single_event( time() - 3 * DAY_IN_SECONDS, 'slowpoke_overdue_task' );
	}
);
add_action( 'slowpoke_overdue_task', '__return_true' );
