<?php
/**
 * Installs a tiny must-use plugin so the profiler can start before every other plugin loads.
 * The must-use plugin does nothing at all unless the request carries Speed Inspector's one-time key.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_Loader {
	const FILE_NAME = '0-jj-speed-inspector.php';

	public static function path() {
		return trailingslashit( WPMU_PLUGIN_DIR ) . self::FILE_NAME;
	}

	public static function contents() {
		$folder = basename( JJSI_DIR );
		return "<?php\n"
			. "/**\n"
			. " * Plugin Name: Speed Inspector (early loader)\n"
			. " * Description: Lets Speed Inspector time how long each plugin takes to load. Does nothing during normal visits. Removed automatically when Speed Inspector is deactivated.\n"
			. " * Version: " . JJSI_VERSION . "\n"
			. " */\n"
			. "defined( 'ABSPATH' ) || exit;\n"
			. "if ( ! empty( \$_GET['jjsi_profile'] ) && ! empty( \$_GET['jjsi_run'] ) ) {\n"
			. "\t\$jjsi_profiler = WP_PLUGIN_DIR . '/" . addslashes( $folder ) . "/includes/class-jjsi-profiler.php';\n"
			. "\tif ( is_readable( \$jjsi_profiler ) ) {\n"
			. "\t\trequire_once \$jjsi_profiler;\n"
			. "\t\tJJSI_Profiler::maybe_start( true );\n"
			. "\t}\n"
			. "}\n";
	}

	public static function is_installed() {
		$path = self::path();
		return is_readable( $path ) && false !== strpos( (string) file_get_contents( $path ), 'jjsi_profile' ); // phpcs:ignore WordPress.WP.AlternativeFunctions
	}

	/** Returns true if the loader is in place. Never throws - the plugin still works without it. */
	public static function install() {
		$path = self::path();
		if ( self::is_installed() && file_get_contents( $path ) === self::contents() ) { // phpcs:ignore WordPress.WP.AlternativeFunctions
			return true;
		}
		if ( ! is_dir( WPMU_PLUGIN_DIR ) && ! wp_mkdir_p( WPMU_PLUGIN_DIR ) ) {
			return false;
		}
		return false !== @file_put_contents( $path, self::contents() ); // phpcs:ignore WordPress.WP.AlternativeFunctions,WordPress.PHP.NoSilencedErrors
	}

	public static function uninstall() {
		$path = self::path();
		if ( self::is_installed() ) {
			@unlink( $path ); // phpcs:ignore WordPress.WP.AlternativeFunctions,WordPress.PHP.NoSilencedErrors
		}
		delete_transient( 'jjsi_token' );
	}
}
