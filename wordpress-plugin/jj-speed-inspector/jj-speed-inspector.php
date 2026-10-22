<?php
/**
 * Plugin Name:       Speed Inspector
 * Plugin URI:        https://github.com/YordalStun/Website-Tester
 * Description:       Finds out exactly what is slowing your site down - which plugins, database queries, calls to other websites, the server or settings - and tells you how to fix it. Go to Tools &gt; Speed Inspector.
 * Version:           1.1.0
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * Author:            JJ Hosting
 * License:           GPLv2 or later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       jj-speed-inspector
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

define( 'JJSI_VERSION', '1.1.0' );
define( 'JJSI_FILE', __FILE__ );
define( 'JJSI_DIR', __DIR__ );

require_once JJSI_DIR . '/includes/class-jjsi-profiler.php';
require_once JJSI_DIR . '/includes/class-jjsi-loader.php';
require_once JJSI_DIR . '/includes/class-jjsi-known.php';
require_once JJSI_DIR . '/includes/class-jjsi-checks.php';
require_once JJSI_DIR . '/includes/class-jjsi-report.php';
require_once JJSI_DIR . '/includes/class-jjsi-runner.php';

// If the must-use loader isn't installed (e.g. mu-plugins isn't writable), profile from here instead.
// Plugins that load before this one then aren't timed while loading, but everything they do later is.
JJSI_Profiler::maybe_start( false );

register_activation_hook( __FILE__, array( 'JJSI_Loader', 'install' ) );
register_deactivation_hook( __FILE__, array( 'JJSI_Loader', 'uninstall' ) );

if ( is_admin() ) {
	require_once JJSI_DIR . '/includes/class-jjsi-admin.php';
	JJSI_Admin::init();
}
JJSI_Runner::init();

if ( defined( 'WP_CLI' ) && WP_CLI ) {
	require_once JJSI_DIR . '/includes/class-jjsi-cli.php';
	WP_CLI::add_command( 'speed-inspector', 'JJSI_CLI' );
}
