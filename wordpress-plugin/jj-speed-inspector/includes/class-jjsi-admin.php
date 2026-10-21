<?php
/**
 * Tools > Speed Inspector.
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_Admin {
	const SLUG = 'jj-speed-inspector';

	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'menu' ) );
		add_action( 'admin_enqueue_scripts', array( __CLASS__, 'assets' ) );
		add_action( 'admin_post_jjsi_export', array( __CLASS__, 'export' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( JJSI_FILE ), array( __CLASS__, 'links' ) );
	}

	public static function menu() {
		add_management_page( 'Speed Inspector', 'Speed Inspector', 'manage_options', self::SLUG, array( __CLASS__, 'page' ) );
	}

	public static function links( $links ) {
		array_unshift( $links, '<a href="' . esc_url( admin_url( 'tools.php?page=' . self::SLUG ) ) . '">Run a test</a>' );
		return $links;
	}

	public static function assets( $hook ) {
		if ( 'tools_page_' . self::SLUG !== $hook ) {
			return;
		}
		$url = plugins_url( 'assets/', JJSI_FILE );
		wp_enqueue_style( 'jjsi', $url . 'admin.css', array(), JJSI_VERSION );
		wp_enqueue_script( 'jjsi', $url . 'admin.js', array(), JJSI_VERSION, true );
		wp_localize_script( 'jjsi', 'JJSI', array( 'ajax' => admin_url( 'admin-ajax.php' ), 'nonce' => wp_create_nonce( 'jjsi' ) ) );
	}

	public static function page() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$report = get_option( JJSI_Runner::REPORT );
		$export = wp_nonce_url( admin_url( 'admin-post.php?action=jjsi_export' ), 'jjsi_export' );
		?>
		<div class="wrap jjsi-wrap">
			<h1>Speed Inspector</h1>
			<p class="jjsi-intro">Finds out what is slowing this site down from the inside: how long each plugin and the theme add to every page, slow database queries, calls to other websites, the server's speed, caching, the database and scheduled tasks.</p>

			<div class="jjsi-card jjsi-controls">
				<p><button type="button" class="button button-primary button-hero" id="jjsi-run">Run speed test</button></p>
				<p><label for="jjsi-extra">Also test this page (optional):</label><br>
					<input type="url" id="jjsi-extra" class="regular-text" placeholder="<?php echo esc_attr( home_url( '/example-page/' ) ); ?>"></p>
				<p class="jjsi-muted">Takes about a minute. The home page, newest post, a page<?php echo function_exists( 'wc_get_page_permalink' ) ? ' and the shop' : ''; ?> are each visited several times as a logged-out visitor with the page cache skipped. Visitors are not affected and nothing is sent outside this site.</p>
				<div id="jjsi-progress" hidden>
					<div class="jjsi-progressbar"><span id="jjsi-progress-fill"></span></div>
					<p id="jjsi-status" class="jjsi-muted"></p>
				</div>
				<?php if ( ! JJSI_Loader::is_installed() ) : ?>
					<p class="jjsi-note">Note: the early loader isn't installed in wp-content/mu-plugins (the folder may not be writable), so the time plugins take to load their files won't be measured. Everything else works.</p>
				<?php endif; ?>
			</div>

			<div id="jjsi-results">
				<?php if ( is_array( $report ) ) : ?>
					<div class="jjsi-card jjsi-export">
						<p><a class="button" href="<?php echo esc_url( $export ); ?>">Export for AI (.md file)</a>
						<span class="jjsi-muted">Downloads everything this test measured in one text file you can give to an AI assistant (or a developer) to analyse. Nothing is sent anywhere by this plugin.</span></p>
					</div>
					<?php echo JJSI_Report::render( $report ); // phpcs:ignore WordPress.Security.EscapeOutput -- escaped inside ?>
				<?php else : ?>
					<div class="jjsi-card"><p>No test has been run yet. Press <b>Run speed test</b>.</p></div>
				<?php endif; ?>
			</div>
			<iframe id="jjsi-frame" title="Speed Inspector test" hidden></iframe>
		</div>
		<?php
	}

	public static function export() {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( 'Not allowed' );
		}
		check_admin_referer( 'jjsi_export' );
		$report = get_option( JJSI_Runner::REPORT );
		if ( ! is_array( $report ) ) {
			wp_die( 'Run a test first.' );
		}
		$raw  = get_option( JJSI_Runner::RAW );
		$host = preg_replace( '/[^a-z0-9.-]/i', '_', (string) wp_parse_url( home_url(), PHP_URL_HOST ) );
		$name = 'speed-inspector-' . $host . '-' . gmdate( 'Y-m-d-Hi', $report['created'] ) . '.md';
		nocache_headers();
		header( 'Content-Type: text/markdown; charset=utf-8' );
		header( 'Content-Disposition: attachment; filename="' . $name . '"' );
		echo JJSI_Report::export( $report, is_array( $raw ) ? $raw : array() ); // phpcs:ignore WordPress.Security.EscapeOutput -- a plain text download
		exit;
	}
}
