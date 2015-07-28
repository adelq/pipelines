#!/usr/bin/env python

"""
ATAC-seq pipeline
"""

from argparse import ArgumentParser
import os
import sys
from . import toolkit as tk
import cPickle as pickle
from pypiper import Pypiper


__author__ = "Andre Rendeiro"
__copyright__ = "Copyright 2015, Andre Rendeiro"
__credits__ = []
__license__ = "GPL2"
__version__ = "0.1"
__maintainer__ = "Andre Rendeiro"
__email__ = "arendeiro@cemm.oeaw.ac.at"
__status__ = "Development"


def main():
    # Parse command-line arguments
    parser = ArgumentParser(
        prog="atacseq-pipeline",
        description="ATAC-seq pipeline."
    )
    parser = mainArgParser(parser)
    args = parser.parse_args()
    # save pickle
    samplePickle = args.samplePickle

    # Read in objects
    prj, sample, args = pickle.load(open(samplePickle, "rb"))

    # Start main function
    process(args, prj, sample)

    # Remove pickle
    if not args.dry_run:
        os.system("rm %s" % samplePickle)

    # Exit
    print("Finished and exiting.")

    sys.exit(0)


def mainArgParser(parser):
    """
    Global options for pipeline.
    """
    # Project
    parser.add_argument(
        dest="samplePickle",
        help="Pickle with tuple of: (pipelines.Project, pipelines.Sample, argparse.ArgumentParser).",
        type=str
    )
    return parser


def process(args, prj, sample):
    """
    This takes unmapped Bam files and makes trimmed, aligned, duplicate marked
    and removed, indexed, shifted Bam files along with a UCSC browser track.
    Peaks are called and filtered.
    """

    print("Start processing ATAC-seq sample %s." % sample.name)

    # Start Pypiper object
    pipe = Pypiper("pipe", sample.dirs.sampleRoot, args=args)

    # Merge Bam files if more than one technical replicate
    if type(sample.unmappedBam) == list:
        pipe.timestamp("Merging bam files from replicates")
        cmd = tk.mergeBams(
            inputBams=sample.unmappedBam,  # this is a list of sample paths
            outputBam=sample.unmapped
        )
        pipe.call_lock(cmd, sample.unmapped, shell=True)
        sample.unmappedBam = sample.unmapped

    # Fastqc
    pipe.timestamp("Measuring sample quality with Fastqc")
    cmd = tk.fastqc(
        inputBam=sample.unmappedBam,
        outputDir=sample.dirs.sampleRoot,
        sampleName=sample.name
    )
    pipe.call_lock(cmd, os.path.join(sample.dirs.sampleRoot, sample.name + "_fastqc.zip"), shell=True)

    # Convert bam to fastq
    pipe.timestamp("Converting to Fastq format")
    cmd = tk.bam2fastq(
        inputBam=sample.unmappedBam,
        outputFastq=sample.fastq1 if sample.paired else sample.fastq,
        outputFastq2=sample.fastq2 if sample.paired else None,
        unpairedFastq=sample.fastqUnpaired if sample.paired else None
    )
    pipe.call_lock(cmd, sample.fastq1 if sample.paired else sample.fastq, shell=True)
    if not sample.paired:
        pipe.clean_add(sample.fastq, conditional=True)
    if sample.paired:
        pipe.clean_add(sample.fastq1, conditional=True)
        pipe.clean_add(sample.fastq2, conditional=True)
        pipe.clean_add(sample.fastqUnpaired, conditional=True)

    # Trim reads
    pipe.timestamp("Trimming adapters from sample")
    if args.trimmer == "trimmomatic":
        cmd = tk.trimmomatic(
            inputFastq1=sample.fastq1 if sample.paired else sample.fastq,
            inputFastq2=sample.fastq2 if sample.paired else None,
            outputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
            outputFastq1unpaired=sample.trimmed1Unpaired if sample.paired else None,
            outputFastq2=sample.trimmed2 if sample.paired else None,
            outputFastq2unpaired=sample.trimmed2Unpaired if sample.paired else None,
            cpus=args.cpus,
            adapters=prj.config["adapters"],
            log=sample.trimlog
        )
        pipe.call_lock(cmd, sample.trimmed1 if sample.paired else sample.trimmed, shell=True)
        if not sample.paired:
            pipe.clean_add(sample.trimmed, conditional=True)
        else:
            pipe.clean_add(sample.trimmed1, conditional=True)
            pipe.clean_add(sample.trimmed1Unpaired, conditional=True)
            pipe.clean_add(sample.trimmed2, conditional=True)
            pipe.clean_add(sample.trimmed2Unpaired, conditional=True)

    elif args.trimmer == "skewer":
        cmd = tk.skewer(
            inputFastq1=sample.fastq1 if sample.paired else sample.fastq,
            inputFastq2=sample.fastq2 if sample.paired else None,
            outputPrefix=os.path.join(sample.dirs.unmapped, sample.name),
            outputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
            outputFastq2=sample.trimmed2 if sample.paired else None,
            trimLog=sample.trimlog,
            cpus=args.cpus,
            adapters=prj.config["adapters"]
        )
        pipe.call_lock(cmd, sample.trimmed1 if sample.paired else sample.trimmed, shell=True)
        if not sample.paired:
            pipe.clean_add(sample.trimmed, conditional=True)
        else:
            pipe.clean_add(sample.trimmed1, conditional=True)
            pipe.clean_add(sample.trimmed2, conditional=True)

    # Map
    pipe.timestamp("Mapping reads with Bowtie2")
    cmd = tk.bowtie2Map(
        inputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
        inputFastq2=sample.trimmed2 if sample.paired else None,
        outputBam=sample.mapped,
        log=sample.alnRates,
        metrics=sample.alnMetrics,
        genomeIndex=prj.config["annotations"]["genomes"][sample.genome],
        maxInsert=args.maxinsert,
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.mapped, shell=True)
    pipe.clean_add(sample.mapped, conditional=True)

    # Filter reads
    pipe.timestamp("Filtering reads for quality")
    cmd = tk.filterReads(
        inputBam=sample.mapped,
        outputBam=sample.filtered,
        metricsFile=sample.dupsMetrics,
        paired=sample.paired,
        cpus=args.cpus,
        Q=args.quality
    )
    pipe.call_lock(cmd, sample.filtered, shell=True)

    # Shift reads
    if sample.tagmented:
        pipe.timestamp("Shifting reads of tagmented sample")
        cmd = tk.shiftReads(
            inputBam=sample.filtered,
            genome=sample.genome,
            outputBam=sample.filteredshifted
        )
        pipe.call_lock(cmd, sample.filteredshifted, shell=True)

    # Index bams
    pipe.timestamp("Indexing bamfiles with samtools")
    cmd = tk.indexBam(inputBam=sample.mapped)
    pipe.call_lock(cmd, sample.mapped + ".bai", shell=True)
    cmd = tk.indexBam(inputBam=sample.filtered)
    pipe.call_lock(cmd, sample.filtered + ".bai", shell=True)
    if sample.tagmented:
        cmd = tk.indexBam(inputBam=sample.filteredshifted)
        pipe.call_lock(cmd, sample.filteredshifted + ".bai", shell=True)

    # Make tracks
    # right now tracks are only made for bams without duplicates
    pipe.timestamp("Making bigWig tracks from bam file")
    cmd = tk.bamToBigWig(
        inputBam=sample.filteredshifted,
        outputBigWig=sample.bigwig,
        genomeSizes=prj.config["annotations"]["chrsizes"][sample.genome],
        genome=sample.genome,
        tagmented=False,  # by default make extended tracks
        normalize=True
    )
    pipe.call_lock(cmd, sample.bigwig, shell=True)
    cmd = tk.addTrackToHub(
        sampleName=sample.name,
        trackURL=sample.trackURL,
        trackHub=os.path.join(prj.dirs.html, "trackHub_{0}.txt".format(sample.genome)),
        colour=sample.trackColour
    )
    pipe.call_lock(cmd, lock_name=sample.name + "addToTrackHub", shell=True)
    tk.linkToTrackHub(
        trackHubURL="/".join([prj.config["url"], prj.name, "trackHub_{0}.txt".format(sample.genome)]),
        fileName=os.path.join(prj.dirs.root, "ucsc_tracks_{0}.html".format(sample.genome)),
        genome=sample.genome
    )

    # Plot fragment distribution
    pipe.timestamp("Plotting insert size distribution")
    tk.plotInsertSizesFit(
        bam=sample.filtered,
        plot=sample.insertplot,
        outputCSV=sample.insertdata
    )

    # Count coverage genome-wide
    pipe.timestamp("Calculating genome-wide coverage")
    cmd = tk.genomeWideCoverage(
        inputBam=sample.filteredshifted,
        genomeWindows=prj.config["annotations"]["genomewindows"][sample.genome],
        output=sample.coverage
    )
    pipe.call_lock(cmd, sample.coverage, shell=True)

    # Calculate NSC, RSC
    pipe.timestamp("Assessing signal/noise in sample")
    cmd = tk.peakTools(
        inputBam=sample.filteredshifted,
        output=sample.qc,
        plot=sample.qcPlot,
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.qcPlot, shell=True, nofail=True)

    # Call peaks
    pipe.timestamp("Calling peaks with MACS2")
    # make dir for output (macs fails if it does not exist)
    if not os.path.exists(sample.dirs.peaks):
        os.makedirs(sample.dirs.peaks)

    cmd = tk.macs2CallPeaksATACSeq(
        treatmentBam=sample.filteredshifted,
        outputDir=sample.dirs.peaks,
        sampleName=sample.name,
        genome=sample.genome
    )
    pipe.call_lock(cmd, sample.peaks, shell=True)

    # # Filter peaks based on mappability regions
    # pipe.timestamp("Filtering peaks in low mappability regions")

    # # get closest read length of sample to available mappability read lengths
    # closestLength = min(prj.config["annotations"]["alignability"][sample.genome].keys(), key=lambda x:abs(x - sample.readLength))

    # cmd = tk.filterPeaksMappability(
    #     peaks=sample.peaks,
    #     alignability=prj.config["annotations"]["alignability"][sample.genome][closestLength],
    #     filteredPeaks=sample.filteredPeaks
    # )
    # pipe.call_lock(cmd, sample.filteredPeaks, shell=True)

    # Calculate fraction of reads in peaks (FRiP)
    pipe.timestamp("Calculating fraction of reads in peaks (FRiP)")
    cmd = tk.calculateFRiP(
        inputBam=sample.filteredshifted,
        inputBed=sample.peaks,
        output=sample.frip
    )
    pipe.call_lock(cmd, sample.frip, shell=True)

    print("Finished processing sample %s." % sample.name)


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except KeyboardInterrupt:
        print("Program canceled by user!")
        sys.exit(1)
