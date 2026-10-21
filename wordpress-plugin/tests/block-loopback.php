<?php
/**
 * Plugin Name: Block loopback (test helper)
 * Description: Pretends the host blocks the site from calling itself, to test Speed Inspector's browser fallback.
 * Only active while the option jjsi_test_block_loopback is set.
 */
add_filter(
	'pre_http_request',
	function ( $pre, $args, $url ) {
		if ( get_option( 'jjsi_test_block_loopback' ) && 0 === strpos( $url, home_url() ) ) {
			return new WP_Error( 'http_request_failed', 'cURL error 7: Failed to connect (blocked for testing)' );
		}
		return $pre;
	},
	10,
	3
);
