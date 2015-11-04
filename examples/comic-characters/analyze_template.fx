use downloader as dld

# Instantiations of this template should set this
# ALIGN = 5 (good or bad)
# EYE = 6 
# HAIR = 7 
TRAIT_COLUMN=x

SEX_COLUMN=8

extract_traits: dld.prep_tsvs
	code.sh:
		cut -f ${TRAIT_COLUMN},${SEX_COLUMN} $PLN(dld,marvel-wikia-data.tsv) | sort > $PLN(marvel_sex_traits.tsv)
		cut -f ${TRAIT_COLUMN},${SEX_COLUMN} $PLN(dld,dc-wikia-data.tsv) | sort > $PLN(dc_sex_traits.tsv)

		egrep "[^[:space:]]+.*Male" $PLN(marvel_sex_traits.tsv) | tr '[:blank:]' ' ' | cut -f 1 -d " " > $PLN(marvel.males)
		egrep "[^[:space:]]+.*Female" $PLN(marvel_sex_traits.tsv) | tr '[:blank:]' ' ' | cut -f 1 -d " " > $PLN(marvel.females)

compute_freqs: extract_traits
	code.py:	
		def compute_freq(diter):
			freqs = {}
			for d in diter:
				d = d.strip()
				freqs[d] = 1 + freqs.get(d,0)
			total = sum(freqs.values())
			freqs = {k:(float(v)/total) for k,v in freqs.items()}

			return freqs

		mfreqs = compute_freq(open('$PLN(marvel.males)','r'))
		ffreqs = compute_freq(open('$PLN(marvel.females)','r'))

		keys = set(mfreqs.keys())
		keys.update(ffreqs.keys())
		keys = {k:ffreqs.get(k,0) for k in keys}.items()
		keys.sort(key=lambda x: -x[1])
		keys = [x[0] for x in keys]

		print '%-10s\\tFemale\\tMale' % 'Value'
		print '---------------------------------'

		for k in keys:
			print '%-10s\\t%1.3f\\t%1.3f' % (k,ffreqs.get(k,0),mfreqs.get(k,0))

		print
