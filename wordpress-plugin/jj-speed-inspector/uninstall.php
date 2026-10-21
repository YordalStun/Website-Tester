<?php
/**
 * Removes everything Speed Inspector stored when the plugin is deleted.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'WP_UNINSTALL_PLUGIN' ) || exit;

foreach ( array( 'jjsi_last_report', 'jjsi_last_raw', 'jjsi_state', 'jjsi_loader_version' ) as $jjsi_option ) {
	delete_option( $jjsi_option );
}
delete_transient( 'jjsi_token' );

$jjsi_loader = trailingslashit( WPMU_PLUGIN_DIR ) . '0-jj-speed-inspector.php';
if ( is_file( $jjsi_loader ) && false !== strpos( (string) file_get_contents( $jjsi_loader ), 'jjsi_profile' ) ) { // phpcs:ignore WordPress.WP.AlternativeFunctions
	@unlink( $jjsi_loader ); // phpcs:ignore WordPress.WP.AlternativeFunctions,WordPress.PHP.NoSilencedErrors
}
