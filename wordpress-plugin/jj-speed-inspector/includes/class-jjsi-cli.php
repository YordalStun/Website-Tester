<?php
/**
 * WP-CLI: wp speed-inspector run [--page=<url>] [--export=<file>] [--format=<text|json>]
 *
 * @package JJ_Speed_Inspector
 */

defined( 'ABSPATH' ) || exit;

class JJSI_CLI {

	/**
	 * Run a speed test and print the result.
	 *
	 * ## OPTIONS
	 *
	 * [--page=<url>]
	 * : Also test this page.
	 *
	 * [--export=<file>]
	 * : Save the "Export for AI" file here.
	 *
	 * [--format=<format>]
	 * : text or json.
	 * ---
	 * default: text
	 * ---
	 *
	 * @param array $args       Positional arguments.
	 * @param array $assoc_args Options.
	 */
	public function run( $args, $assoc_args ) {
		JJSI_Loader::install();
		$report = JJSI_Runner::run_all(
			isset( $assoc_args['page'] ) ? $assoc_args['page'] : '',
			function ( $i, $total, $step ) {
				WP_CLI::log( sprintf( '[%d/%d] %s %s', $i, $total, $step['do'], isset( $step['url'] ) ? $step['url'] : '' ) );
			}
		);
		if ( ! $report ) {
			WP_CLI::error( 'The test did not run.' );
		}
		if ( ! empty( $assoc_args['export'] ) ) {
			file_put_contents( $assoc_args['export'], JJSI_Report::export( $report, get_option( JJSI_Runner::RAW, array() ) ) ); // phpcs:ignore WordPress.WP.AlternativeFunctions
			WP_CLI::log( 'Export saved to ' . $assoc_args['export'] );
		}
		if ( isset( $assoc_args['format'] ) && 'json' === $assoc_args['format'] ) {
			WP_CLI::line( wp_json_encode( $report, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES ) );
			return;
		}
		WP_CLI::line( '' );
		WP_CLI::line( sprintf( 'Grade %s (%s/100) - %s', $report['grade'], $report['score'], $report['diagnosis']['headline'] ) );
		foreach ( $report['diagnosis']['paragraphs'] as $p ) {
			WP_CLI::line( '  ' . $p );
		}
		WP_CLI::line( '' );
		WP_CLI::line( 'Where the time goes (average uncached page):' );
		foreach ( $report['breakdown'] as $row ) {
			WP_CLI::line( sprintf( '  %-32s %6d ms', $row['label'], $row['ms'] ) );
		}
		WP_CLI::line( '' );
		WP_CLI::line( 'Time per plugin:' );
		foreach ( array_slice( $report['owners'], 0, 12 ) as $row ) {
			WP_CLI::line( sprintf( '  %-45s %8.1f ms  %3d queries  %s', $row['label'], $row['ms'], $row['queries'], implode( ', ', array_unique( $row['http'] ) ) ) );
		}
		WP_CLI::line( '' );
		WP_CLI::line( 'Findings:' );
		foreach ( $report['findings'] as $f ) {
			WP_CLI::line( sprintf( '  [%s] (%s) %s', strtoupper( $f['severity'] ), $f['category'], $f['title'] ) );
		}
	}
}
