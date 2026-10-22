<?php
/**
 * Plugin Name: Slow loopback (test helper)
 * Description: Pretends the server is slow to reach its own website (like a broken DNS resolver), to test Speed Inspector.
 * Only active while the option jjsi_test_slow_loopback holds a delay in milliseconds.
 */
add_action(
	'http_api_curl',
	function ( $handle, $args, $url ) {
		$delay = (int) get_option( 'jjsi_test_slow_loopback' );
		if ( $delay > 0 && 0 === strpos( $url, home_url() ) ) {
			usleep( $delay * 1000 );
		}
	},
	10,
	3
);
